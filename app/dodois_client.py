"""Dodo IS API client.

На данном этапе токен читаем из settings.dodo_is_access_token (env
DODO_IS_ACCESS_TOKEN). В проде заменится на чтение из Postgres соседнего
сервиса, у которого уже настроен OAuth refresh flow.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

import httpx

from .config import settings


log = logging.getLogger(__name__)

# Большие окна (полный месяц × несколько юнитов) Dodo IS отдаёт небыстро —
# по 30–60 сек на запрос. Делаем таймаут с запасом.
_REQ_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

# Максимум ОДНОВРЕМЕННЫХ HTTP-запросов к Dodo IS со всего процесса.
# До 2026-06-10 константа была мёртвой (нигде не применялась, см.
# code-review B4) — теперь это реальный asyncio.Semaphore вокруг каждого
# запроса. История: parallel-fan-out ~144 запросов за один /api/board
# перегружал Dodo IS — TCP-соединения дропались.
_MAX_PARALLEL = 6

_semaphore = asyncio.Semaphore(_MAX_PARALLEL)

# Один переиспользуемый httpx-клиент на процесс (keep-alive вместо
# нового TLS-хендшейка на каждый вызов — раньше /api/board открывал
# ~15 клиентов за запрос). Закрывается в shutdown-хуке main.py.
_shared_client: Optional[httpx.AsyncClient] = None


def _client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=_REQ_TIMEOUT,
            limits=httpx.Limits(
                max_connections=_MAX_PARALLEL,
                max_keepalive_connections=_MAX_PARALLEL,
            ),
        )
    return _shared_client


async def aclose_shared_client() -> None:
    """Закрыть общий клиент. Вызывается из shutdown-хука FastAPI."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


async def _get(
    url: str, token: str, params: Optional[dict[str, Any]] = None,
) -> httpx.Response:
    """Единая точка GET к Dodo IS: общий клиент + глобальный семафор.
    Семафор держится только на время HTTP-вызова (не на время backoff-sleep
    в _with_retries), чтобы ожидающие ретраи не занимали слоты."""
    async with _semaphore:
        return await _client().get(url, headers=_headers(token), params=params)

# Сетевые ошибки, на которые имеет смысл ретраить: transport-level, без ответа
# от сервера. 4xx/5xx прилетают как DodoISError из _raise — их не ретраим,
# чтобы не маскировать auth-проблемы.
_TRANSIENT_EXC = (
    httpx.ReadError,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.WriteError,
)


class DodoISError(Exception):
    """Обёртка над ошибками Dodo IS API."""


class DodoISRateLimit(DodoISError):
    """Специальный случай 429: содержит retry_after_seconds, по которому
    `_with_retries` спит вместо обычного экспоненциального бэкоффа."""

    def __init__(self, message: str, retry_after_seconds: float = 5.0):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


async def _with_retries(
    op_name: str,
    fn: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 4,
    base_delay: float = 1.5,
) -> Any:
    """Выполнить async-операцию с retry на транзиентные сетевые ошибки
    и rate-limit (429). Экспоненциальный backoff с джиттером для
    сетевых; для 429 — пауза, которую запросил сам Dodo IS.

    Ретраит:
      - сетевые transient (_TRANSIENT_EXC) — экспоненциально 1.5/3/6с
      - DodoISRateLimit (429) — спит retry_after_seconds + джиттер

    НЕ ретраит остальные DodoISError (4xx/5xx) — это не транзиентные,
    обычно проблема с токеном/правами/валидацией.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except _TRANSIENT_EXC as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, 0.5)
            log.warning(
                "Dodo IS %s: transient %s (attempt %d/%d), retry in %.1fs",
                op_name, type(exc).__name__, attempt, attempts, delay,
            )
            await asyncio.sleep(delay)
        except DodoISRateLimit as exc:
            last_exc = exc
            if attempt == attempts:
                break
            # Dodo IS сам сказал сколько ждать. Добавляем небольшой
            # джиттер чтобы все ретраи разлетелись и не выстрелили вместе.
            delay = exc.retry_after_seconds + random.uniform(0.5, 2.0)
            log.warning(
                "Dodo IS %s: rate limit (attempt %d/%d), wait %.1fs",
                op_name, attempt, attempts, delay,
            )
            await asyncio.sleep(delay)
    raise DodoISError(
        f"Dodo IS {op_name}: ошибка после {attempts} попыток: "
        f"{type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def _headers(token: str) -> dict[str, str]:
    """Bearer-заголовок. Token приходит из app.auth.tokens.get_dodois_token()
    в вызывающей точке (main.py), не из settings. Это даёт per-user
    разделение токенов и позволяет соседскому сервису обновлять access_token
    в БД без перезапуска нашего сервиса."""
    if not token:
        raise DodoISError("Empty Dodo IS token passed to client")
    return {"Authorization": f"Bearer {token}"}


def _raise(response: httpx.Response) -> None:
    if response.is_success:
        return
    # Dodo IS отдаёт JSON-ошибки в двух форматах; нам важна понятность
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:400]}
    # 429 = rate limit. Dodo IS отдаёт message вида "Try again in 19 seconds".
    if response.status_code == 429:
        retry_after = 5.0
        # Standard HTTP header (если есть)
        ra_hdr = response.headers.get("Retry-After")
        if ra_hdr and ra_hdr.isdigit():
            retry_after = float(ra_hdr)
        # Fallback: парсим из текста сообщения "Try again in N seconds"
        else:
            msg = (body.get("message") if isinstance(body, dict) else "") or ""
            import re as _re
            m = _re.search(r"(\d+)\s*seconds?", msg)
            if m:
                retry_after = float(m.group(1))
        raise DodoISRateLimit(
            f"Dodo IS API 429 {response.request.url.path}: {body}",
            retry_after_seconds=retry_after,
        )
    raise DodoISError(
        f"Dodo IS API {response.status_code} {response.request.url.path}: {body}"
    )


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ─── Batched helper ───────────────────────────────────────────────────
# Dodo IS принимает до 30 юнитов в одном запросе для большинства
# endpoint'ов через comma-separated `units`. Делает 1 HTTP-запрос на
# партию вместо N параллельных — сильно снижает шанс rate-limit и
# дроп TCP-соединений со стороны Dodo IS.

_BATCH_SIZE = 30  # лимит Dodo IS на units в одном запросе


async def _batched_get(
    op_name: str,
    url: str,
    token: str,
    unit_uuids: list[str],
    *,
    response_key: str,
    extra_params: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Generic GET с batching по units (до 30 за раз).

    Возвращает плоский список из `response_key` всех батчей.
    Передаваемый `extra_params` присоединяется к params каждого батча.
    """
    if not unit_uuids:
        return []
    batches = [
        unit_uuids[i:i + _BATCH_SIZE]
        for i in range(0, len(unit_uuids), _BATCH_SIZE)
    ]
    out: list[dict[str, Any]] = []
    for idx, batch in enumerate(batches):
        params: dict[str, Any] = {"units": ",".join(batch)}
        if extra_params:
            params.update(extra_params)

        async def _do() -> list[dict[str, Any]]:
            r = await _get(url, token, params)
            _raise(r)
            return r.json().get(response_key) or []

        batch_label = f"{op_name}[batch={len(batch)}"
        if len(batches) > 1:
            batch_label += f",{idx + 1}/{len(batches)}"
        batch_label += "]"
        out.extend(await _with_retries(batch_label, _do))
    return out


async def fetch_units(token: str) -> list[dict[str, Any]]:
    """GET /auth/roles/units — список всех юнитов пользователя."""
    url = f"{settings.dodo_is_auth_url}/roles/units"
    r = await _get(url, token)
    _raise(r)
    data = r.json()
    return data if isinstance(data, list) else []


async def fetch_productivity_many(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> list[dict[str, Any]]:
    """BATCHED: 1 запрос на партию до 30 юнитов через `units` comma-separated.
    Drastически снижает нагрузку: 1 HTTP-вызов вместо N параллельных."""
    return await _batched_get(
        op_name="productivity",
        url=f"{settings.dodo_is_base_url}/production/productivity",
        token=token,
        unit_uuids=unit_uuids,
        response_key="productivityStatistics",
        extra_params={"from": _fmt(from_date), "to": _fmt(to_date)},
    )


async def fetch_delivery_statistics(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> list[dict[str, Any]]:
    """BATCHED: 1 запрос на партию до 30 юнитов."""
    return await _batched_get(
        op_name="delivery-stats",
        url=f"{settings.dodo_is_base_url}/delivery/statistics",
        token=token,
        unit_uuids=unit_uuids,
        response_key="unitsStatistics",
        extra_params={"from": _fmt(from_date), "to": _fmt(to_date)},
    )


async def fetch_late_delivery_vouchers(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> list[dict[str, Any]]:
    """BATCHED + paginated: 1 запрос на партию (до 30 юнитов) с пагинацией
    по 1000. Для дня сети из 6 точек обычно <100 vouchers → 1 запрос."""
    if not unit_uuids:
        return []
    take = 1000
    url = f"{settings.dodo_is_base_url}/delivery/vouchers"
    batches = [
        unit_uuids[i:i + _BATCH_SIZE]
        for i in range(0, len(unit_uuids), _BATCH_SIZE)
    ]
    out: list[dict[str, Any]] = []
    for batch in batches:
        skip = 0
        while True:
            params = {
                "from": _fmt(from_date), "to": _fmt(to_date),
                "units": ",".join(batch), "take": take, "skip": skip,
            }

            async def _do() -> dict[str, Any]:
                r = await _get(url, token, params)
                _raise(r)
                return r.json()

            data = await _with_retries(
                f"vouchers[batch={len(batch)}@{skip}]", _do,
            )
            page = data.get("vouchers") or []
            out.extend(page)
            if data.get("isEndOfListReached") or len(page) < take:
                break
            skip += take
    return out


async def fetch_late_delivery_vouchers_count(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> dict[str, int]:
    items = await fetch_late_delivery_vouchers(token, unit_uuids, from_date, to_date)
    counts: dict[str, int] = {}
    for it in items:
        uid = (it.get("unitId") or "").lower().replace("-", "")
        if not uid:
            continue
        counts[uid] = counts.get(uid, 0) + 1
    return counts


async def fetch_orders_handover_statistics(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime,
    *, sales_channels: str | None = None,
) -> list[dict[str, Any]]:
    """BATCHED режим: один HTTP-запрос на всю партию юнитов (через запятую
    в `units`). Dodo IS принимает до 30 юнитов в одном запросе; для бОльших
    сетей бьём на батчи по 30.

    Drastически снижает нагрузку: для сети из N≤30 юнитов — 1 запрос
    вместо N (раньше делали per-unit gather).

    `sales_channels`=None → все каналы, 'DineIn' → только ресторан,
    'Delivery' → только доставка.
    """
    if not unit_uuids:
        return []

    _BATCH_SIZE = 30  # лимит Dodo IS
    batches = [
        unit_uuids[i:i + _BATCH_SIZE]
        for i in range(0, len(unit_uuids), _BATCH_SIZE)
    ]

    url = f"{settings.dodo_is_base_url}/production/orders-handover-statistics"
    out: list[dict[str, Any]] = []

    async def _fetch_batch(batch: list[str]) -> list[dict]:
        params: dict[str, Any] = {
            "from": _fmt(from_date),
            "to": _fmt(to_date),
            "units": ",".join(batch),
        }
        if sales_channels:
            params["salesChannels"] = sales_channels

        async def _do() -> list[dict]:
            r = await _get(url, token, params)
            _raise(r)
            return r.json().get("ordersHandoverStatistics") or []

        label_ch = f":{sales_channels}" if sales_channels else ""
        return await _with_retries(
            f"handover-stats[batch={len(batch)}{label_ch}]", _do,
        )

    # Если батчей несколько (>30 юнитов) — берём их последовательно,
    # чтобы не плодить параллельные запросы и не дёргать rate-limit.
    for batch in batches:
        out.extend(await _fetch_batch(batch))

    return out


async def fetch_sales_by_channel(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime,
) -> dict[str, dict[str, Any]]:
    """BATCHED. Возвращает {unit_uuid_normalized: {channels, total, name}}.

    GET /accounting/sales — детальный список чеков с пагинацией. Batched:
    1 запрос на партию до 30 юнитов через `units` comma-separated. Если
    sales > 1000 — пагинируем по `skip/take` в пределах батча.

    Агрегируем по unitId:
        channels: {salesChannel: sum(products[].priceWithDiscount)}
        total:    sum по всем чекам и каналам
        name:     unitName из первого чека (нужен для display)
    Используется для окна «до часа» — accounting/sales принимает arbitrary
    precision времени (в отличие от productivity/finance/monthly).

    `unit_uuid_normalized` = lowercase без дефисов — единый формат.
    """
    if not unit_uuids:
        return {}

    # Заготовка result-словаря с дефолтами для каждого юнита (даже если
    # за окно у него 0 продаж).
    def _norm(u: str) -> str:
        return u.lower().replace("-", "")

    out: dict[str, dict[str, Any]] = {
        _norm(u): {"channels": {}, "total": 0.0, "name": None}
        for u in unit_uuids
    }

    url = f"{settings.dodo_is_base_url}/accounting/sales"
    take = 1000

    batches = [
        unit_uuids[i:i + _BATCH_SIZE]
        for i in range(0, len(unit_uuids), _BATCH_SIZE)
    ]

    for batch in batches:
        skip = 0
        while True:
            params = {
                "units": ",".join(batch),
                "from": _fmt(from_date), "to": _fmt(to_date),
                "skip": skip, "take": take,
            }

            async def _do() -> dict[str, Any]:
                r = await _get(url, token, params)
                _raise(r)
                return r.json()

            data = await _with_retries(
                f"sales[batch={len(batch)}@{skip}]", _do,
            )
            items = data.get("sales") or []
            for sale in items:
                uid = _norm(sale.get("unitId") or "")
                if uid not in out:
                    continue  # неизвестный юнит, не должен случиться
                entry = out[uid]
                if entry["name"] is None and sale.get("unitName"):
                    entry["name"] = sale["unitName"]
                ch = sale.get("salesChannel") or "Other"
                for p in sale.get("products") or []:
                    price = float(p.get("priceWithDiscount") or 0)
                    entry["channels"][ch] = entry["channels"].get(ch, 0.0) + price
                    entry["total"] += price
            if data.get("isEndOfListReached") or len(items) < take:
                break
            skip += take

    return out


async def fetch_finance_sales_monthly(
    token: str, unit_uuids: list[str], from_date: str, to_date: str,
) -> list[dict[str, Any]]:
    """BATCHED. Месячные продажи по заведениям. Даты 'YYYY-MM-DD'.
    Channels — Delivery / Dine-in / Takeaway (готовая разбивка)."""
    return await _batched_get(
        op_name="finance-monthly",
        url=f"{settings.dodo_is_base_url}/finances/sales/units/monthly",
        token=token,
        unit_uuids=unit_uuids,
        response_key="result",
        extra_params={"fromDate": from_date, "toDate": to_date},
    )


async def fetch_incentives_by_members(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    """BATCHED. Вознаграждения сотрудников за период по партии юнитов.
    Поля staffMember: shiftsDetailing[].unitId / premiums[].unitId."""
    return await _batched_get(
        op_name="incentives",
        url=f"{settings.dodo_is_base_url}/staff/incentives-by-members",
        token=token,
        unit_uuids=unit_uuids,
        response_key="staffMembers",
        extra_params={"from": _fmt(from_date), "to": _fmt(to_date)},
    )


# ─── Stop-sales (стопы продаж) ─────────────────────────────────────────
# 4 endpoint'а с разной типизацией стопа:
#   /production/stop-sales-channels       — каналы (Доставка/Ресторан/Самовывоз)
#   /delivery/stop-sales-sectors          — сектора доставки (живут на /delivery!)
#   /production/stop-sales-products       — продукты в продаже
#   /production/stop-sales-ingredients    — ингредиенты
#
# Все принимают одни и те же query: from / to / units. Ответ — массив
# с разными именами корневого ключа (см. документацию dodois). Каждая запись
# имеет endedAtLocal=null если стоп ещё активен; для дашборда нас в основном
# интересуют ИМЕННО активные стопы (видны прямо сейчас).
#
# Делаем по одному запросу на юнит — те же ограничения concurrency.


async def fetch_stop_sales_channels(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    """BATCHED. Стопы каналов продаж за окно. Поля строки:
    `unitId`, `salesChannelName`, `reason`,
    `startedAtLocal`, `endedAtLocal` (null если активен)."""
    return await _batched_get(
        op_name="stop-channels",
        url=f"{settings.dodo_is_base_url}/production/stop-sales-channels",
        token=token,
        unit_uuids=unit_uuids,
        response_key="stopSalesBySalesChannels",
        extra_params={"from": _fmt(from_date), "to": _fmt(to_date)},
    )


async def fetch_stop_sales_sectors(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    """BATCHED. Стопы секторов доставки. Поля: `unitId`, `sectorName`,
    `isSubSector`, `startedAtLocal`, `endedAtLocal`.
    NB: endpoint живёт на /delivery (не /production)."""
    return await _batched_get(
        op_name="stop-sectors",
        url=f"{settings.dodo_is_base_url}/delivery/stop-sales-sectors",
        token=token,
        unit_uuids=unit_uuids,
        response_key="stopSalesBySectors",
        extra_params={"from": _fmt(from_date), "to": _fmt(to_date)},
    )


async def fetch_stop_sales_products(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    """BATCHED. Стопы продуктов. Поля: `unitId`, `productName`, `reason`,
    `startedAtLocal`, `endedAtLocal`.
    NB: root-ключ в единственном числе — `stopSalesByProduct`."""
    return await _batched_get(
        op_name="stop-products",
        url=f"{settings.dodo_is_base_url}/production/stop-sales-products",
        token=token,
        unit_uuids=unit_uuids,
        response_key="stopSalesByProduct",
        extra_params={"from": _fmt(from_date), "to": _fmt(to_date)},
    )


async def fetch_stop_sales_ingredients(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    """BATCHED. Стопы ингредиентов. Поля: `unitId`, `ingredientName`,
    `ingredientCategoryName`, `reason`, `startedAtLocal`, `endedAtLocal`."""
    return await _batched_get(
        op_name="stop-ingredients",
        url=f"{settings.dodo_is_base_url}/production/stop-sales-ingredients",
        token=token,
        unit_uuids=unit_uuids,
        response_key="stopSalesByIngredients",
        extra_params={"from": _fmt(from_date), "to": _fmt(to_date)},
    )


# ─── Курьеры на смене (real-time) ─────────────────────────────────────
# /staff/couriers-on-shift возвращает массив курьеров, находящихся на
# смене ПРЯМО СЕЙЧАС (если `on` пустой). Доступ требует scope
# `staffshifts:read`.


async def fetch_couriers_on_shift(
    token: str, unit_uuids: list[str],
) -> list[dict[str, Any]]:
    """BATCHED. Курьеры на смене сейчас. 1 запрос на партию (до 30 юнитов).
    Поля строки: `id`, `unitId`, `positionName`, `clockInAtLocal`, ..."""
    return await _batched_get(
        op_name="couriers-shift",
        url=f"{settings.dodo_is_base_url}/staff/couriers-on-shift",
        token=token,
        unit_uuids=unit_uuids,
        response_key="couriers",
        # `on` не передаём — endpoint берёт «сейчас»
    )


async def fetch_couriers_orders(
    token: str, unit_uuids: list[str],
    from_date: datetime, to_date: datetime,
    *, take: int = 200,
) -> list[dict[str, Any]]:
    """BATCHED. Заказы курьеров за окно (с пагинацией внутри батча).
    Используется для расчёта «курьер сейчас в пути» (handedOver без
    deliveryTime). Take=200 обычно с запасом для 1ч окна."""
    if not unit_uuids:
        return []
    url = f"{settings.dodo_is_base_url}/delivery/couriers-orders"
    batches = [
        unit_uuids[i:i + _BATCH_SIZE]
        for i in range(0, len(unit_uuids), _BATCH_SIZE)
    ]
    out: list[dict[str, Any]] = []
    for batch in batches:
        skip = 0
        while True:
            params = {
                "units": ",".join(batch),
                "from": _fmt(from_date), "to": _fmt(to_date),
                "take": take, "skip": skip,
            }
            async def _do() -> dict[str, Any]:
                r = await _get(url, token, params)
                _raise(r)
                return r.json()
            data = await _with_retries(
                f"couriers-orders[batch={len(batch)}@{skip}]", _do,
            )
            page = data.get("couriersOrders") or []
            out.extend(page)
            if data.get("isEndOfListReached") or len(page) < take:
                break
            skip += take
    return out


