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
from typing import Any, Awaitable, Callable

import httpx

from .config import settings


log = logging.getLogger(__name__)

# Большие окна (полный месяц × несколько юнитов) Dodo IS отдаёт небыстро —
# по 30–60 сек на запрос. Делаем таймаут с запасом.
_REQ_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

# Параллелизм per-unit запросов. Dodo IS под нагрузкой 6+ одновременных
# больших окон рвёт коннекты с ReadError — бьёмся максимум по 3 в параллель.
_MAX_PARALLEL = 3

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


async def _with_retries(
    op_name: str,
    fn: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 4,
    base_delay: float = 1.5,
) -> Any:
    """Выполнить async-операцию с retry на транзиентные сетевые ошибки.

    Экспоненциальный backoff с джиттером: ~1.5s, ~3s, ~6s. Не ретраит
    DodoISError (HTTP 4xx/5xx) — это не сетевой сбой и обычно означает
    проблему с токеном/правами.
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
    raise DodoISError(
        f"Dodo IS {op_name}: сетевая ошибка после {attempts} попыток: "
        f"{type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def _ensure_token() -> str:
    token = settings.dodo_is_access_token
    if not token:
        raise DodoISError(
            "DODO_IS_ACCESS_TOKEN не задан. Положите токен в .env или "
            "передайте через переменную окружения."
        )
    return token


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_ensure_token()}"}


def _raise(response: httpx.Response) -> None:
    if response.is_success:
        return
    # Dodo IS отдаёт JSON-ошибки в двух форматах; нам важна понятность
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:400]}
    raise DodoISError(
        f"Dodo IS API {response.status_code} {response.request.url.path}: {body}"
    )


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


async def fetch_units() -> list[dict[str, Any]]:
    """GET /auth/roles/units — список всех юнитов пользователя."""
    url = f"{settings.dodo_is_auth_url}/roles/units"
    async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as http:
        r = await http.get(url, headers=_headers())
        _raise(r)
        data = r.json()
    # API отдаёт массив
    return data if isinstance(data, list) else []


async def fetch_productivity(
    unit_uuid: str, from_date: datetime, to_date: datetime
) -> dict[str, Any] | None:
    """GET /production/productivity — productivityStatistics по одному юниту.

    Возвращает первый (единственный) элемент productivityStatistics или None.
    """
    url = f"{settings.dodo_is_base_url}/production/productivity"
    params = {
        "from": _fmt(from_date),
        "to": _fmt(to_date),
        "units": unit_uuid,
    }
    async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as http:
        r = await http.get(url, headers=_headers(), params=params)
        _raise(r)
        data = r.json()
    items = data.get("productivityStatistics") or []
    return items[0] if items else None


async def _fetch_productivity_one(
    http: httpx.AsyncClient, sem: asyncio.Semaphore, unit_uuid: str,
    from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    url = f"{settings.dodo_is_base_url}/production/productivity"
    params = {
        "from": _fmt(from_date),
        "to": _fmt(to_date),
        "units": unit_uuid,
    }

    async def _do() -> list[dict[str, Any]]:
        async with sem:
            r = await http.get(url, headers=_headers(), params=params)
        _raise(r)
        return r.json().get("productivityStatistics") or []

    return await _with_retries(f"productivity[{unit_uuid[:8]}]", _do)


async def fetch_productivity_many(
    unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> list[dict[str, Any]]:
    """Параллельные per-unit запросы с retry и concurrency-limit.

    Multi-unit запросом Dodo IS на больших окнах (полный месяц × 6 юнитов)
    часто отваливается по таймауту, а юниты независимы — поэтому асинхронно
    собираем по одному, но не более _MAX_PARALLEL одновременно: при 6+
    параллельных запросах сервер срывает коннекты с ReadError.
    """
    if not unit_uuids:
        return []
    sem = asyncio.Semaphore(_MAX_PARALLEL)
    async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as http:
        results = await asyncio.gather(
            *[_fetch_productivity_one(http, sem, u, from_date, to_date) for u in unit_uuids]
        )
    out: list[dict[str, Any]] = []
    for r in results:
        out.extend(r)
    return out


async def _fetch_delivery_stats_one(
    http: httpx.AsyncClient, sem: asyncio.Semaphore, unit_uuid: str,
    from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    url = f"{settings.dodo_is_base_url}/delivery/statistics"
    params = {
        "from": _fmt(from_date),
        "to": _fmt(to_date),
        "units": unit_uuid,
    }

    async def _do() -> list[dict[str, Any]]:
        async with sem:
            r = await http.get(url, headers=_headers(), params=params)
        _raise(r)
        return r.json().get("unitsStatistics") or []

    return await _with_retries(f"delivery-stats[{unit_uuid[:8]}]", _do)


async def fetch_delivery_statistics(
    unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> list[dict[str, Any]]:
    """GET /delivery/statistics — агрегированная стата доставки по юнитам.

    Параллельные per-unit запросы (см. fetch_productivity_many).
    Полезные поля: deliveryOrdersCount, lateOrdersCount, deliverySales.
    """
    if not unit_uuids:
        return []
    sem = asyncio.Semaphore(_MAX_PARALLEL)
    async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as http:
        results = await asyncio.gather(
            *[_fetch_delivery_stats_one(http, sem, u, from_date, to_date) for u in unit_uuids]
        )
    out: list[dict[str, Any]] = []
    for r in results:
        out.extend(r)
    return out


async def _fetch_vouchers_one(
    http: httpx.AsyncClient, sem: asyncio.Semaphore, unit_uuid: str,
    from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    """Постранично выкачать vouchers по одному юниту.

    Каждая страница оборачивается в _with_retries отдельно — чтобы один
    flaky-запрос на N-й странице не валил всю выкачку.
    """
    url = f"{settings.dodo_is_base_url}/delivery/vouchers"
    take = 1000
    skip = 0
    out: list[dict[str, Any]] = []
    while True:
        params = {
            "from": _fmt(from_date),
            "to": _fmt(to_date),
            "units": unit_uuid,
            "take": take,
            "skip": skip,
        }

        async def _do() -> dict[str, Any]:
            async with sem:
                r = await http.get(url, headers=_headers(), params=params)
            _raise(r)
            return r.json()

        data = await _with_retries(
            f"vouchers[{unit_uuid[:8]}@{skip}]", _do
        )
        page = data.get("vouchers") or []
        out.extend(page)
        if data.get("isEndOfListReached") or len(page) < take:
            break
        skip += take
    return out


async def fetch_late_delivery_vouchers(
    unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> list[dict[str, Any]]:
    """GET /delivery/vouchers — сертификаты за опоздание.

    Параллельно по юнитам (с лимитом concurrency), с пагинацией внутри юнита.
    """
    if not unit_uuids:
        return []
    sem = asyncio.Semaphore(_MAX_PARALLEL)
    async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as http:
        results = await asyncio.gather(
            *[_fetch_vouchers_one(http, sem, u, from_date, to_date) for u in unit_uuids]
        )
    out: list[dict[str, Any]] = []
    for r in results:
        out.extend(r)
    return out


async def fetch_late_delivery_vouchers_count(
    unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> dict[str, int]:
    """Считает количество сертификатов за опоздание по каждому юниту в периоде.

    Возвращает {unit_uuid_lower: count}. UUID нормализуется к lower-case без дефисов
    для устойчивого матчинга (Dodo IS отдаёт в разных форматах).
    """
    items = await fetch_late_delivery_vouchers(unit_uuids, from_date, to_date)
    counts: dict[str, int] = {}
    for it in items:
        uid = (it.get("unitId") or "").lower().replace("-", "")
        if not uid:
            continue
        counts[uid] = counts.get(uid, 0) + 1
    return counts
