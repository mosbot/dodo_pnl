"""
Dodo IS API — MCP Server
========================
MCP-сервер для работы с Dodo IS API (https://docs.dodois.io).

Транспорты:
  stdio (по умолчанию)  — для Claude Desktop / Claude Code
  http                  — для custom connector в claude.ai (streamable HTTP)

Переменные окружения (.env поддерживается):
  DODO_CLIENT_ID      — client id приложения
  DODO_CLIENT_SECRET  — client secret приложения
  DODO_COUNTRY        — код страны (ru, hr, ...), по умолчанию ru
  DODO_UNITS          — UUID заведений по умолчанию, через запятую (без дефисов или с — неважно)
  DODO_UNITS_ALIASES  — необязательный JSON: {"кубинка": "uuid", "тучково": "uuid", ...}
  DODO_TOKENS_FILE    — путь к файлу с токенами (по умолчанию ./tokens.json)
  MCP_TRANSPORT       — stdio | http (по умолчанию stdio)
  MCP_HOST, MCP_PORT, MCP_PATH — для http-транспорта (0.0.0.0, 8788, /mcp)

Источник токена (по приоритету):
  1) Брокер SA — если заданы SA_TOKEN_BROKER_URL, SA_INTERNAL_TOKEN, DODO_SUB:
     GET {SA_TOKEN_BROKER_URL}?sub={DODO_SUB} с заголовком X-Admin-Token.
     SA сам держит refresh-токены живыми (beat раз в час + тихий refresh),
     повторная авторизация MCP не нужна никогда. Рекомендуемый режим.
  2) Legacy: tokens.json — первичная авторизация через auth_setup.py (OAuth2
     PKCE), дальше сервер обновляет токены сам по refresh_token.
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Optional

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from pydantic import Field

load_dotenv()

AUTH_URL = os.getenv("DODO_AUTH_URL", "https://auth.dodois.io")
API_URL = os.getenv("DODO_API_URL", "https://api.dodois.io")
COUNTRY = os.getenv("DODO_COUNTRY", "ru")
CLIENT_ID = os.getenv("DODO_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DODO_CLIENT_SECRET", "")
TOKENS_FILE = Path(os.getenv("DODO_TOKENS_FILE", Path(__file__).parent / "tokens.json"))
# Брокер токенов SA (см. докстринг модуля). Все три — иначе legacy tokens.json.
SA_TOKEN_BROKER_URL = os.getenv("SA_TOKEN_BROKER_URL", "").strip()
SA_INTERNAL_TOKEN = os.getenv("SA_INTERNAL_TOKEN", "").strip()
DODO_SUB = os.getenv("DODO_SUB", "").strip()
USE_SA_BROKER = bool(SA_TOKEN_BROKER_URL and SA_INTERNAL_TOKEN and DODO_SUB)
SA_BROKER_CACHE_SEC = int(os.getenv("SA_BROKER_CACHE_SEC", "120"))
DEFAULT_UNITS = [u.strip() for u in os.getenv("DODO_UNITS", "").split(",") if u.strip()]
try:
    UNIT_ALIASES: dict[str, str] = {
        k.lower(): v for k, v in json.loads(os.getenv("DODO_UNITS_ALIASES", "{}")).items()
    }
except json.JSONDecodeError:
    UNIT_ALIASES = {}

MAX_RESPONSE_CHARS = 90_000


# ---------------------------------------------------------------- token store
class TokenManager:
    """Хранит access_token в памяти, обновляет через refresh_token из файла."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0

    def _load(self) -> dict:
        if not TOKENS_FILE.exists():
            raise RuntimeError(
                f"Файл токенов не найден: {TOKENS_FILE}. "
                "Запустите auth_setup.py для первичной авторизации."
            )
        return json.loads(TOKENS_FILE.read_text())

    def _save(self, data: dict) -> None:
        TOKENS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def _refresh(self) -> None:
        tokens = self._load()
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise RuntimeError("В tokens.json нет refresh_token — повторите auth_setup.py.")
        resp = httpx.post(
            f"{AUTH_URL}/connect/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Не удалось обновить токен ({resp.status_code}): {resp.text[:500]}"
            )
        data = resp.json()
        self._access_token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 300)) - 30
        # IdentityServer может ротировать refresh_token — сохраняем новый
        if data.get("refresh_token"):
            tokens["refresh_token"] = data["refresh_token"]
        tokens["access_token"] = data["access_token"]
        tokens["obtained_at"] = int(time.time())
        self._save(tokens)

    def _fetch_from_sa(self) -> None:
        """Токен у брокера SA. Срок брокер не отдаёт — кэшируем коротко
        (SA_BROKER_CACHE_SEC), при 401 от API кэш сбрасывается (invalidate)."""
        resp = httpx.get(
            SA_TOKEN_BROKER_URL,
            params={"sub": DODO_SUB},
            headers={"X-Admin-Token": SA_INTERNAL_TOKEN},
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Брокер SA не отдал токен ({resp.status_code}): {resp.text[:300]}"
            )
        token = (resp.json().get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Брокер SA вернул пустой access_token")
        self._access_token = token
        self._expires_at = time.time() + SA_BROKER_CACHE_SEC

    def get(self) -> str:
        with self._lock:
            if not self._access_token or time.time() >= self._expires_at:
                if USE_SA_BROKER:
                    self._fetch_from_sa()
                else:
                    self._refresh()
            assert self._access_token
            return self._access_token

    def invalidate(self) -> None:
        """Сбросить кэш (после 401 от Dodo IS) — следующий get() перезапросит."""
        with self._lock:
            self._access_token = None
            self._expires_at = 0.0


tokens = TokenManager()


# ---------------------------------------------------------------- http helper
def resolve_units(units: Optional[list[str]]) -> list[str]:
    """Подставляет юниты по умолчанию и разворачивает алиасы (названия городов)."""
    if not units:
        if not DEFAULT_UNITS:
            raise ValueError(
                "Не указаны units и не задан DODO_UNITS в окружении. "
                "Получите UUID заведений инструментом list_my_units."
            )
        return DEFAULT_UNITS
    resolved = []
    for u in units:
        key = u.strip().lower()
        resolved.append(UNIT_ALIASES.get(key, u.strip()))
    return resolved


def api_get(path: str, params: Optional[dict[str, Any]] = None) -> Any:
    """GET-запрос к Dodo IS API с авторизацией, обработкой ошибок и обрезкой ответа."""
    clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    # units как comma-separated без дефисов — API принимает оба формата UUID
    if "units" in clean and isinstance(clean["units"], list):
        clean["units"] = ",".join(u.replace("-", "") for u in clean["units"])
    url = f"{API_URL}{path}"
    resp = httpx.get(
        url,
        params=clean,
        headers={"Authorization": f"Bearer {tokens.get()}"},
        timeout=60,
    )
    if resp.status_code == 401 and USE_SA_BROKER:
        # Кэшированный токен мог быть обновлён брокером — один повтор со свежим.
        tokens.invalidate()
        resp = httpx.get(
            url, params=clean,
            headers={"Authorization": f"Bearer {tokens.get()}"}, timeout=60,
        )
    if resp.status_code == 401:
        hint = ("токен брокера SA отклонён — проверьте DODO_SUB и вход этого аккаунта в sa"
                if USE_SA_BROKER else
                "проверьте scopes приложения или переавторизуйтесь (auth_setup.py)")
        return {"error": f"401 Unauthorized — {hint}."}
    if resp.status_code == 403:
        return {"error": f"403 Forbidden — нет прав на {path}. Проверьте scopes и роли пользователя в Dodo IS."}
    if resp.status_code >= 400:
        return {"error": f"{resp.status_code}: {resp.text[:1000]}", "url": str(resp.url)}
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return {"raw": resp.text[:MAX_RESPONSE_CHARS]}
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > MAX_RESPONSE_CHARS:
        return {
            "warning": f"Ответ обрезан ({len(text)} симв.). Используйте skip/take или сузьте период.",
            "truncated_data": json.loads(text[:MAX_RESPONSE_CHARS].rsplit(",", 1)[0] + "]}")
            if False else text[:MAX_RESPONSE_CHARS],
        }
    return data


def paged_get(path: str, params: dict[str, Any], fetch_all: bool, page_size: int = 1000,
              max_pages: int = 30) -> Any:
    """Автопагинация по skip/take для эндпоинтов с большими выборками."""
    if not fetch_all:
        return api_get(path, params)
    all_rows: list = []
    skip = 0
    key_hint = None
    for _ in range(max_pages):
        page = api_get(path, {**params, "skip": skip, "take": page_size})
        if isinstance(page, dict) and "error" in page:
            return page
        # ответы обычно вида {"<entity>": [...], "isEndOfListReached": bool}
        rows = None
        end = True
        if isinstance(page, dict):
            for k, v in page.items():
                if isinstance(v, list):
                    rows, key_hint = v, k
            end = page.get("isEndOfListReached", len(rows or []) < page_size)
        elif isinstance(page, list):
            rows = page
            end = len(rows) < page_size
        if rows:
            all_rows.extend(rows)
        if end or not rows:
            break
        skip += page_size
    result = {key_hint or "items": all_rows, "totalFetched": len(all_rows)}
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > MAX_RESPONSE_CHARS:
        return {
            "warning": f"Слишком много данных ({len(all_rows)} записей). Показаны первые записи; сузьте период.",
            "items": all_rows[: max(1, int(len(all_rows) * MAX_RESPONSE_CHARS / len(text)))],
            "totalFetched": len(all_rows),
        }
    return result


mcp = FastMCP(
    "dodois",
    instructions=(
        "MCP-сервер Dodo IS API для управления пиццериями Dodo Pizza. "
        f"Страна по умолчанию: {COUNTRY}. "
        "Если units не указаны — используются заведения владельца по умолчанию. "
        "Даты — ISO 8601 (2026-07-01T00:00:00). Начните с list_my_units, чтобы узнать UUID заведений."
    ),
)

UnitsParam = Annotated[
    Optional[list[str]],
    Field(description="UUID заведений (или алиасы-названия, если настроены). Пусто = заведения по умолчанию."),
]
FromParam = Annotated[str, Field(description="Начало периода, ISO 8601, напр. 2026-07-01T00:00:00")]
ToParam = Annotated[str, Field(description="Конец периода, ISO 8601")]
CountryParam = Annotated[Optional[str], Field(description=f"Код страны (по умолчанию {COUNTRY})")]


def cc(country: Optional[str]) -> str:
    return (country or COUNTRY).lower()


# ================================================================ Auth / Units
@mcp.tool()
def list_my_units(country: CountryParam = None) -> Any:
    """Список заведений (пиццерий), доступных пользователю, с их UUID. Первый шаг для работы с остальными инструментами."""
    return api_get("/auth/roles/units", {"take": 100})


# ================================================================ Учёт (продажи)
@mcp.tool()
def get_sales(
    from_date: FromParam,
    to_date: ToParam,
    units: UnitsParam = None,
    order_source: Annotated[Optional[str], Field(description="CallCenter|Website|Dine-in|MobileApp|Manager|Aggregator|Kiosk")] = None,
    sales_channel: Annotated[Optional[str], Field(description="Dine-in|Takeaway|Delivery")] = None,
    fetch_all: Annotated[bool, Field(description="Выгрузить все страницы автоматически")] = False,
    skip: int = 0,
    take: int = 500,
    country: CountryParam = None,
) -> Any:
    """Продажи (чеки) по заведениям за период: суммы, каналы, источники заказов."""
    params = {
        "from": from_date, "to": to_date, "units": resolve_units(units),
        "orderSource": order_source, "salesChannel": sales_channel,
        "skip": skip, "take": take,
    }
    return paged_get(f"/dodopizza/{cc(country)}/accounting/sales", params, fetch_all)


@mcp.tool()
def get_cancelled_sales(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None,
    fetch_all: bool = False, skip: int = 0, take: int = 500, country: CountryParam = None,
) -> Any:
    """Отменённые заказы за период (для анализа отмен и потерь)."""
    params = {"from": from_date, "to": to_date, "units": resolve_units(units), "skip": skip, "take": take}
    return paged_get(f"/dodopizza/{cc(country)}/accounting/cancelled-sales", params, fetch_all)


@mcp.tool()
def get_inventory_stocks(
    units: UnitsParam = None,
    stock_items: Annotated[Optional[list[str]], Field(description="UUID сырья для фильтра")] = None,
    skip: int = 0, take: int = 500, country: CountryParam = None,
) -> Any:
    """Текущие складские остатки сырья по заведениям."""
    return api_get(
        f"/dodopizza/{cc(country)}/accounting/inventory-stocks",
        {"units": resolve_units(units),
         "stockItems": ",".join(s.replace("-", "") for s in stock_items) if stock_items else None,
         "skip": skip, "take": take},
    )


@mcp.tool()
def get_stock_consumption(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None,
    skip: int = 0, take: int = 500, country: CountryParam = None,
) -> Any:
    """Расход сырья за период."""
    return api_get(
        f"/dodopizza/{cc(country)}/accounting/stock-consumptions-by-period",
        {"from": from_date, "to": to_date, "units": resolve_units(units), "skip": skip, "take": take},
    )


@mcp.tool()
def get_write_offs(
    from_date: FromParam, to_date: ToParam,
    kind: Annotated[str, Field(description="products | stock-items")] = "products",
    units: UnitsParam = None, skip: int = 0, take: int = 500, country: CountryParam = None,
) -> Any:
    """Списания за период: готовые продукты (products) или сырьё (stock-items)."""
    return api_get(
        f"/dodopizza/{cc(country)}/accounting/write-offs/{kind}",
        {"from": from_date, "to": to_date, "units": resolve_units(units), "skip": skip, "take": take},
    )


@mcp.tool()
def get_incoming_stock_items(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None,
    skip: int = 0, take: int = 500, country: CountryParam = None,
) -> Any:
    """Приходы сырья (поставки) за период."""
    return api_get(
        f"/dodopizza/{cc(country)}/accounting/incoming-stock-items",
        {"from": from_date, "to": to_date, "units": resolve_units(units), "skip": skip, "take": take},
    )


# ================================================================ Доставка
@mcp.tool()
def get_delivery_statistics(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None, country: CountryParam = None,
) -> Any:
    """Сводная статистика доставки: количество заказов, среднее время, опоздания, сертификаты, трипы курьеров. Время в UTC."""
    return api_get(
        f"/dodopizza/{cc(country)}/delivery/statistics",
        {"from": from_date, "to": to_date, "units": resolve_units(units)},
    )


@mcp.tool()
def get_couriers_orders(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None,
    fetch_all: bool = False, skip: int = 0, take: int = 500, country: CountryParam = None,
) -> Any:
    """Заказы курьеров за период: детально по каждой доставке (времена, сектора, курьер). Время в UTC."""
    params = {"from": from_date, "to": to_date, "units": resolve_units(units), "skip": skip, "take": take}
    return paged_get(f"/dodopizza/{cc(country)}/delivery/couriers-orders", params, fetch_all)


@mcp.tool()
def get_stop_sales_sectors(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None, country: CountryParam = None,
) -> Any:
    """Стоп-продажи по секторам доставки за период (UTC)."""
    return api_get(
        f"/dodopizza/{cc(country)}/delivery/stop-sales-sectors",
        {"from": from_date, "to": to_date, "units": resolve_units(units)},
    )


@mcp.tool()
def get_late_delivery_vouchers(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None,
    skip: int = 0, take: int = 500, country: CountryParam = None,
) -> Any:
    """Сертификаты за опоздание доставки за период."""
    return api_get(
        f"/dodopizza/{cc(country)}/delivery/vouchers",
        {"from": from_date, "to": to_date, "units": resolve_units(units), "skip": skip, "take": take},
    )


# ================================================================ Команда
@mcp.tool()
def get_staff_members(
    units: UnitsParam = None,
    staff_type: Annotated[Optional[str], Field(description="Operator|KitchenMember|Courier|Cashier|PersonalManager")] = None,
    statuses: Annotated[Optional[str], Field(description="напр. Active,Suspended,Dismissed")] = None,
    fetch_all: bool = False, skip: int = 0, take: int = 200, country: CountryParam = None,
) -> Any:
    """Список сотрудников заведений."""
    params = {"units": resolve_units(units), "staffTypeName": staff_type,
              "statuses": statuses, "skip": skip, "take": take}
    return paged_get(f"/dodopizza/{cc(country)}/staff/members", params, fetch_all, page_size=200)


@mcp.tool()
def get_staff_shifts(
    clock_in_from: Annotated[str, Field(description="Начало периода начала смен, ISO 8601")],
    clock_in_to: Annotated[str, Field(description="Конец периода начала смен, ISO 8601")],
    units: UnitsParam = None,
    staff_type: Annotated[Optional[str], Field(description="Operator|KitchenMember|Courier|Cashier|PersonalManager")] = None,
    fetch_all: bool = False, skip: int = 0, take: int = 500, country: CountryParam = None,
) -> Any:
    """Смены сотрудников за период: часы, ставки, выработка. Основа для расчёта ФОТ и часовой нагрузки."""
    params = {"clockInFrom": clock_in_from, "clockInTo": clock_in_to,
              "units": resolve_units(units), "staffTypeName": staff_type,
              "skip": skip, "take": take}
    return paged_get(f"/dodopizza/{cc(country)}/staff/shifts", params, fetch_all)


@mcp.tool()
def get_couriers_on_shift(units: UnitsParam = None, country: CountryParam = None) -> Any:
    """Курьеры на смене прямо сейчас."""
    return api_get(
        f"/dodopizza/{cc(country)}/staff/couriers-on-shift",
        {"units": resolve_units(units)},
    )


@mcp.tool()
def get_staff_incentives(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None,
    skip: int = 0, take: int = 500, country: CountryParam = None,
) -> Any:
    """Вознаграждения сотрудников за период (по сотрудникам)."""
    return api_get(
        f"/dodopizza/{cc(country)}/staff/incentives-by-members",
        {"from": from_date, "to": to_date, "units": resolve_units(units), "skip": skip, "take": take},
    )


# ================================================================ Производство
@mcp.tool()
def get_production_productivity(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None, country: CountryParam = None,
) -> Any:
    """Производительность заведений: выручка на трудочас, заказы в час и т.д. (UTC)."""
    return api_get(
        f"/dodopizza/{cc(country)}/production/productivity",
        {"from": from_date, "to": to_date, "units": resolve_units(units)},
    )


@mcp.tool()
def get_orders_handover_time(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None,
    sales_channels: Annotated[Optional[str], Field(description="Delivery,DineIn,TakeAway")] = None,
    country: CountryParam = None,
) -> Any:
    """Время выдачи заказов (скорость производства) за период (UTC)."""
    return api_get(
        f"/dodopizza/{cc(country)}/production/orders-handover-time",
        {"from": from_date, "to": to_date, "units": resolve_units(units),
         "salesChannels": sales_channels},
    )


@mcp.tool()
def get_stop_sales(
    from_date: FromParam, to_date: ToParam,
    by: Annotated[str, Field(description="products | ingredients | channels")] = "products",
    units: UnitsParam = None, country: CountryParam = None,
) -> Any:
    """Стоп-продажи производства за период: по продуктам, ингредиентам или каналам (UTC)."""
    return api_get(
        f"/dodopizza/{cc(country)}/production/stop-sales-{by}",
        {"from": from_date, "to": to_date, "units": resolve_units(units)},
    )


# ================================================================ Заведения
@mcp.tool()
def get_unit_shifts(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None,
    skip: int = 0, take: int = 500, country: CountryParam = None,
) -> Any:
    """Смены заведений (кассовые смены) за период: выручка, наличные/безнал."""
    return api_get(
        f"/dodopizza/{cc(country)}/units/shifts",
        {"from": from_date, "to": to_date, "units": resolve_units(units), "skip": skip, "take": take},
    )


@mcp.tool()
def get_orders_clients_statistics(
    from_date: FromParam, to_date: ToParam, units: UnitsParam = None, country: CountryParam = None,
) -> Any:
    """Статистика по новым/вернувшимся клиентам за период."""
    return api_get(
        f"/dodopizza/{cc(country)}/orders/clients-statistics",
        {"from": from_date, "to": to_date, "units": resolve_units(units)},
    )


# ================================================================ Финансы (готовые агрегаты)
@mcp.tool()
def get_finance_sales(
    scale: Annotated[str, Field(description="daily | monthly")],
    from_date: Annotated[str, Field(description="YYYY-MM-DD. Лимит окна: daily ≤10 дней, monthly ≤62 дня")],
    to_date: Annotated[str, Field(description="YYYY-MM-DD")],
    units: UnitsParam = None, country: CountryParam = None,
) -> Any:
    """Готовые агрегаты продаж по заведениям: выручка (без VAT) + число заказов с разбивкой по каналам (Delivery/Dine-in/Takeaway). daily — по дням, monthly — по месяцам."""
    return api_get(
        f"/dodopizza/{cc(country)}/finances/sales/units/{scale}",
        {"fromDate": from_date, "toDate": to_date, "units": resolve_units(units)},
    )


# ================================================================ Контроллинг (РКО/РС, проверки)
RatingParam = Annotated[str, Field(description="customer-experience (РКО) | standards (РС)")]


@mcp.tool()
def get_rating_history(
    rating: RatingParam, from_date: Annotated[str, Field(description="YYYY-MM-DD")],
    to_date: Annotated[str, Field(description="YYYY-MM-DD")], units: UnitsParam = None,
) -> Any:
    """История рейтингов по периодам (РКО — недельные, РС — ~2 проверки/мес). Только Calculated+Published периоды. База /controlling (без страны)."""
    return api_get(
        f"/controlling/ratings/{rating}/history",
        {"units": resolve_units(units), "fromDate": from_date, "toDate": to_date},
    )


@mcp.tool()
def get_checkups(
    from_date: Annotated[str, Field(description="YYYY-MM-DD (по дате проведения проверки)")],
    to_date: Annotated[str, Field(description="YYYY-MM-DD")],
    units: UnitsParam = None,
    rating_kind: Annotated[Optional[str], Field(description="CustomerExperience | Standards (пусто = обе)")] = None,
    fetch_all: Annotated[bool, Field(description="Автопагинация")] = False,
) -> Any:
    """Проверки пиццерий, учтённые в рейтингах (в т.ч. «проверки менеджеров смены»). Отменённые/незавершённые не возвращаются."""
    return paged_get(
        "/controlling/checkups",
        {"units": resolve_units(units), "fromDate": from_date, "toDate": to_date,
         "ratingKind": rating_kind},
        fetch_all,
    )


@mcp.tool()
def get_checkup_details(
    checkup_id: Annotated[str, Field(description="UUID проверки из get_checkups")],
) -> Any:
    """Детали проверки: заказ, фото, замечания и баллы по критериям (за что сняли баллы РКО/РС)."""
    return api_get(f"/controlling/checkups/{checkup_id}")


# ================================================================ Обратная связь клиентов
@mcp.tool()
def get_customer_ratings(
    from_date: Annotated[str, Field(description="YYYY-MM-DD. Окно ≤31 дня; «сегодня» недоступно — to максимум вчера")],
    to_date: Annotated[str, Field(description="YYYY-MM-DD")], units: UnitsParam = None,
) -> Any:
    """Средняя оценка заказов клиентами (0..5) по залу и доставке + количество оценок. База /customer-feedback."""
    return api_get(
        "/customer-feedback/customer-ratings",
        {"from": from_date, "to": to_date, "units": resolve_units(units)},
    )


@mcp.tool()
def get_lfl_by_units(
    from_date: Annotated[str, Field(description="YYYY-MM-DD")],
    to_date: Annotated[str, Field(description="YYYY-MM-DD")],
    units: UnitsParam = None,
    granularity: Annotated[Optional[str], Field(description="Гранулярность (см. спеку; пусто = за период)")] = None,
) -> Any:
    """ГОТОВЫЙ Like-for-Like от Dodo IS по заведениям: lflRevenue и lflOrder (доли, напр. 0.05 = +5%)."""
    return api_get(
        "/customer-feedback/lfl/by-units",
        {"from": from_date, "to": to_date, "units": resolve_units(units),
         "granularity": granularity},
    )


# ================================================================ Инвентаризация (ревизии)
@mcp.tool()
def get_revisions(
    from_date: Annotated[str, Field(description="YYYY-MM-DD")],
    to_date: Annotated[str, Field(description="YYYY-MM-DD")],
    units: UnitsParam = None,
    type_of_periodicity: Annotated[Optional[str], Field(description="Day | Week | Month — период ревизии")] = None,
    fetch_all: Annotated[bool, Field(description="Автопагинация")] = False,
) -> Any:
    """Ревизии (инвентаризации): потери/избытки по сырью. Сырьё может повторяться в разрезе одной ревизии (разные места хранения). База /dodopizza/inventory (без страны)."""
    return paged_get(
        "/dodopizza/inventory/revisions",
        {"units": resolve_units(units), "from": from_date, "to": to_date,
         "typeOfPeriodicity": type_of_periodicity},
        fetch_all,
    )


# ================================================================ Универсальный
@mcp.tool()
def dodois_get(
    path: Annotated[str, Field(description=(
        "Путь эндпоинта Dodo IS API. Базы: /dodopizza/{country}/... (основной), "
        "/controlling/... (рейтинги/проверки), /customer-feedback/... (оценки/LFL), "
        "/dodopizza/inventory/... (ревизии), /accounting, /staff, /franchisee, "
        "/marketplace. Полный список: https://docs.dodois.io"))],
    params: Annotated[Optional[dict[str, Any]], Field(description="Query-параметры (units можно списком UUID)")] = None,
) -> Any:
    """Универсальный GET к любому эндпоинту Dodo IS API — для всего, что не покрыто отдельными инструментами (поставщики, продукты, рейтинги, расписания, цели на месяц, вакансии и т.д.)."""
    p = dict(params or {})
    if "units" in p and isinstance(p["units"], list):
        p["units"] = resolve_units(p["units"])
    return api_get(path if path.startswith("/") else f"/{path}", p)


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "http":
        mcp.run(
            transport="http",
            host=os.getenv("MCP_HOST", "0.0.0.0"),
            port=int(os.getenv("MCP_PORT", "8788")),
            path=os.getenv("MCP_PATH", "/mcp"),
        )
    else:
        mcp.run()
