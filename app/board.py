"""Логика страницы `/board` — оперативная сводка по сети.

Источники:
- /production/productivity     — total revenue для всех 5 окон (час-aware)
- /accounting/sales            — channels для today и last_week (час-aware,
                                  пагинация по 1k чеков)
- /finances/sales/units/monthly — channels для MTD, MTD_LFL (с partial date
                                  range — endpoint поддерживает) и LY_full
                                  (полный прошлый месяц для прогноза)

Кэш (in-memory):
- Layer 1 (live, TTL 60с): today + MTD (значения двигаются при каждом часе/минуте)
- Layer 2 (до конца текущего часа MSK): last_week + MTD_LFL
- Layer 3 (immutable в БД): LY_full_month — записываем в monthly_revenue_history

Структура ответа описана в endpoint'е `/api/board`.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from . import dodois_client, store
from .day_window import BoardWindows, MSK, compute_board_windows, forecast_month

log = logging.getLogger(__name__)


# ─── Конфиг ops-метрик на rich-card ────────────────────────────────────
# Полный список ops-метрик, которые рисуются в Подробном виде /board.
# Каждая метрика принадлежит группе (kitchen/delivery) и имеет label.
# Пользователь может выключить любую через /settings (таблица
# board_card_metric_visibility), по умолчанию все видны.
BOARD_OPS_METRICS: list[dict] = [
    {"code": "sales_per_hour",       "group": "kitchen",  "label": "₽/чел·ч"},
    {"code": "products_per_hour",    "group": "kitchen",  "label": "шт/чел·ч"},
    {"code": "cooking_hall_sec",     "group": "kitchen",  "label": "Готовка · зал"},
    {"code": "cooking_delivery_sec", "group": "kitchen",  "label": "Готовка · доставка"},
    {"code": "orders_per_courier_hour", "group": "delivery", "label": "Заказов на курьера"},
    {"code": "orders_per_trip",      "group": "delivery", "label": "Заказов за поездку"},
    {"code": "avg_delivery_sec",     "group": "delivery", "label": "Среднее доставки"},
    {"code": "heated_shelf_sec",     "group": "delivery", "label": "На полке"},
    {"code": "vouchers_count",       "group": "delivery", "label": "Сертификаты"},
    {"code": "couriers",             "group": "delivery", "label": "Курьеры"},
]
BOARD_OPS_METRIC_CODES = {m["code"] for m in BOARD_OPS_METRICS}


# Бюджет на каждый non-critical fetch (стопы, ops-метрики). Лучше
# дашборд без обогащения, чем 502 Gateway timeout.
_STOPS_BUDGET_SEC = 8.0
_OPS_BUDGET_SEC = 15.0
# handover-statistics — самый тяжёлый из ops endpoint'ов: dodo обрабатывает
# заказы пиццерия-за-пиццерию, плюс мы делаем 4 запроса (Delivery+DineIn ×
# today+LW), упираясь в общий семафор. Даём больше воздуха.
_HANDOVER_BUDGET_SEC = 30.0


async def _safe_fetch_stops(
    fetch_fn,
    *args,
    op_name: str,
) -> list[dict[str, Any]]:
    """Обёртка над fetch_stop_sales_*: при любой ошибке (403 InsufficientScopes,
    transient network, и т.д.) или превышении бюджета возвращает пустой список.
    Так дашборд продолжает работать без stops, если у токена нет `stopsales`
    скоупа или Dodo IS медленно отвечает по этому endpoint'у.
    """
    try:
        return await asyncio.wait_for(fetch_fn(*args), timeout=_STOPS_BUDGET_SEC)
    except asyncio.TimeoutError:
        log.warning("board: %s skipped: timeout after %.1fs",
                    op_name, _STOPS_BUDGET_SEC)
        return []
    except dodois_client.DodoISError as e:
        log.warning("board: %s skipped: %s", op_name, str(e)[:200])
        return []
    except Exception:
        log.exception("board: %s failed", op_name)
        return []


async def _safe_fetch_ops(
    fetch_fn,
    *args,
    op_name: str,
    default,
    budget_sec: float = _OPS_BUDGET_SEC,
):
    """Обёртка над fetch_productivity_many / fetch_delivery_statistics /
    fetch_late_delivery_vouchers_count: при ошибке возвращает `default`
    (обычно [] или {})."""
    try:
        return await asyncio.wait_for(fetch_fn(*args), timeout=budget_sec)
    except asyncio.TimeoutError:
        log.warning("board: %s skipped: timeout after %.1fs",
                    op_name, budget_sec)
        return default
    except dodois_client.DodoISError as e:
        log.warning("board: %s skipped: %s", op_name, str(e)[:200])
        return default
    except Exception:
        log.exception("board: %s failed", op_name)
        return default


def _ops_metric(
    current: Optional[float],
    baseline: Optional[float],
    *,
    lower_is_better: bool = False,
    is_time: bool = False,
) -> dict:
    """Универсальный блок ops-метрики: value/baseline/delta(%)/lower_is_better/time.
    Фронт смотрит `lower_is_better` чтобы инвертировать цвет."""
    return {
        "value": current,
        "baseline": baseline,
        "delta": ((current - baseline)
                  if (current is not None and baseline is not None) else None),
        "delta_pct": _delta_pct(current, baseline) if not is_time else None,
        "lower_is_better": lower_is_better,
        "is_time": is_time,
    }


def _index_by_unit(items: list[dict], key: str = "unitId") -> dict[str, dict]:
    return {_normalize_uuid(it.get(key, "")): it for it in items}


# TTL для кэша имён пиццерий. Имена меняются раз в месяцы.
_UNITS_CACHE_TTL_SEC = 24 * 60 * 60  # 24 часа


async def _get_or_refresh_unit_names(
    session: AsyncSession, token: str,
) -> dict[str, str]:
    """Вернуть {uuid_norm: name} из dodois_units_cache. Если кэш пуст
    или старше TTL — обновить из /auth/roles/units (один запрос).

    На любую ошибку API возвращаем что есть в кэше — даже устаревшее лучше
    чем ничего; если кэш пуст, вернём пустой dict (board покажет
    project_id вместо имени, что не катастрофа)."""
    cached = await store.get_units_cache(session)
    age_sec = await store.get_units_cache_max_age_seconds(session)

    needs_refresh = (
        age_sec is None  # таблица пустая
        or age_sec > _UNITS_CACHE_TTL_SEC
    )

    if needs_refresh:
        try:
            units_list = await asyncio.wait_for(
                dodois_client.fetch_units(token), timeout=8.0,
            )
            new_items: dict[str, str] = {}
            for u in units_list or []:
                uid_n = _normalize_uuid(u.get("id", ""))
                nm = u.get("name")
                if uid_n and nm:
                    new_items[uid_n] = nm
            if new_items:
                await store.upsert_units_cache(session, new_items)
                await session.commit()
                log.info(
                    "board: units cache refreshed: %d entries", len(new_items),
                )
                # Reload to get the updated dict (with new refreshed_at)
                cached = await store.get_units_cache(session)
        except Exception as e:
            log.warning(
                "board: fetch_units refresh failed: %s (using stale cache)",
                str(e)[:200],
            )

    return {uid: row["name"] for uid, row in cached.items()}


# Mapping Dodo IS salesChannel → наши ключи. Из доки: Delivery / Dine-in /
# Takeaway. У нас только delivery + restaurant; Takeaway сейчас игнорируем
# (его доля в сети маленькая, в дизайне отдельной линии нет). Если будет
# нужно — добавим как «takeaway».
DELIVERY_CHANNEL = "Delivery"
RESTAURANT_CHANNEL = "Dine-in"

# Маппинг salesChannelName из stop-sales-channels → русская подпись.
# Если приходит другое значение — отображаем как есть.
_CHANNEL_RU = {
    "Delivery": "Доставка",
    "Dine-in": "Ресторан",
    "Takeaway": "Самовывоз",
    "Доставка": "Доставка",
    "Ресторан": "Ресторан",
}


def _parse_iso_local(s: Optional[str]) -> Optional[datetime]:
    """Парсим *AtLocal из dodo IS как naive datetime (это локальное время
    точки, для РФ-сети = MSK). Возвращаем aware-MSK для арифметики."""
    if not s:
        return None
    try:
        # ISO без timezone — типичный формат "2026-06-04T13:12:00"
        dt = datetime.fromisoformat(s.replace("Z", ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK)
        return dt.astimezone(MSK)
    except ValueError:
        return None


def _stop_duration_minutes(
    started: Optional[datetime], ended: Optional[datetime], now: datetime,
) -> Optional[int]:
    """Длительность активного стопа в минутах от startedAtLocal до now.
    Если stop уже закрыт (endedAtLocal) — возвращаем None (нас интересуют
    только активные)."""
    if started is None or ended is not None:
        return None
    delta = now - started
    sec = max(0, int(delta.total_seconds()))
    return sec // 60


def _stop_history(
    rows: list[dict], *, uid_norm: str, name_field: str,
    day_start: datetime, now: datetime, name_map: Optional[dict] = None,
    limit: int = 50,
) -> list[dict]:
    """Все стопы юнита за сегодня (активные + завершённые) как интервалы,
    обрезанные по [day_start, now]. Для таймлайна «история за день».

    Возвращает [{name, started_at, ended_at, minutes, active}], в
    хронологическом порядке (по фактическому началу в пределах дня).
    Стопы, целиком закончившиеся до day_start (вчерашние), отбрасываем.
    """
    out: list[dict] = []
    for r in rows:
        if _normalize_uuid(r.get("unitId", "")) != uid_norm:
            continue
        started = _parse_iso_local(r.get("startedAtLocal"))
        if started is None:
            continue
        ended = _parse_iso_local(r.get("endedAtLocal"))
        end_eff = ended or now
        if end_eff <= day_start:
            continue  # стоп целиком до начала дня
        start_eff = max(started, day_start)
        minutes = max(0, int((end_eff - start_eff).total_seconds()) // 60)
        nm = r.get(name_field) or "—"
        if name_map:
            nm = name_map.get(nm, nm)
        carried = started < day_start  # стоп начался вчера, тянется в сегодня
        out.append({
            "name": nm,
            # Для дневного таймлайна показываем НАЧАЛО В ПРЕДЕЛАХ ДНЯ
            # (обрезанное к 00:00), чтобы интервал и длительность сходились.
            "started_at": start_eff.strftime("%Y-%m-%dT%H:%M:%S"),
            "ended_at": r.get("endedAtLocal"),
            "minutes": minutes,
            "active": ended is None,
            "carried": carried,
            "_sort": start_eff,
        })
    out.sort(key=lambda x: x["_sort"])
    for x in out:
        x.pop("_sort", None)
    return out[:limit]


def _build_stops_for_unit(
    *,
    uid_norm: str,
    now_msk: datetime,
    day_start: datetime,
    raw_channels: list[dict],
    raw_sectors: list[dict],
    raw_products: list[dict],
    raw_ingredients: list[dict],
) -> dict:
    """Собирает блок stops для одного проекта, оставляя только АКТИВНЫЕ
    стопы (endedAtLocal == null). Сортирует от старых к новым (более
    длительные стопы — выше).

    Дополнительно собирает `history` — все стопы за сегодня (активные и
    завершённые) с интервалами, для таймлайна, плюс суммарный простой по
    каналам за день (`downtime_by_channel`)."""
    def _active(rows: list[dict], name_field: str, extra_fields: tuple = ()) -> list[dict]:
        out: list[dict] = []
        for r in rows:
            if _normalize_uuid(r.get("unitId", "")) != uid_norm:
                continue
            started = _parse_iso_local(r.get("startedAtLocal"))
            ended = _parse_iso_local(r.get("endedAtLocal"))
            minutes = _stop_duration_minutes(started, ended, now_msk)
            if minutes is None:
                continue
            item = {
                "name": r.get(name_field) or "—",
                "started_at": r.get("startedAtLocal"),
                "minutes": minutes,
            }
            for ef in extra_fields:
                if ef in r:
                    item[ef] = r[ef]
            out.append(item)
        # Самые старые стопы (= с большим minutes) — наверх
        out.sort(key=lambda x: x["minutes"], reverse=True)
        return out

    channels = _active(raw_channels, "salesChannelName")
    # Русифицируем имена каналов
    for c in channels:
        c["name"] = _CHANNEL_RU.get(c["name"], c["name"])

    sectors = _active(raw_sectors, "sectorName")
    products = _active(raw_products, "productName")
    ingredients = _active(
        raw_ingredients, "ingredientName",
        extra_fields=("ingredientCategoryName",),
    )

    # История за день (интервалы) — каналы и секторы доставки. Продукты/
    # ингредиенты в таймлайн не тащим (детальный шум), оставляем как active.
    hist_channels = _stop_history(
        raw_channels, uid_norm=uid_norm, name_field="salesChannelName",
        day_start=day_start, now=now_msk, name_map=_CHANNEL_RU,
    )
    hist_sectors = _stop_history(
        raw_sectors, uid_norm=uid_norm, name_field="sectorName",
        day_start=day_start, now=now_msk,
    )
    downtime_by_channel: dict[str, int] = {}
    for it in hist_channels:
        downtime_by_channel[it["name"]] = downtime_by_channel.get(it["name"], 0) + it["minutes"]

    return {
        "channels": channels,
        "sectors": sectors,
        "products": products,
        "ingredients": ingredients,
        "history": {
            "channels": hist_channels,
            "sectors": hist_sectors,
            "downtime_by_channel": downtime_by_channel,
        },
    }


def _normalize_uuid(s: str) -> str:
    return (s or "").lower().replace("-", "")


def _aggregate_channels(by_ch: dict[str, float]) -> dict[str, float]:
    """Привести разнообразные salesChannel значения к нашим двум каналам."""
    return {
        "delivery": float(by_ch.get(DELIVERY_CHANNEL, 0) or 0),
        "restaurant": float(by_ch.get(RESTAURANT_CHANNEL, 0) or 0),
    }


def _delta_pct(current: Optional[float], baseline: Optional[float]) -> Optional[float]:
    """((cur / base) − 1). None если current нет (сегодня без данных —
    например смена не открыта) или baseline <= 0."""
    if current is None or baseline is None or baseline <= 0:
        return None
    return current / baseline - 1.0


def _fmt_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


async def _fetch_for_window(
    token: str, unit_uuids: list[str], from_dt: datetime, to_dt: datetime,
) -> dict[str, dict]:
    """Параллельно дёргает productivity для всех юнитов за одно окно.
    Возвращает {unit_uuid_normalized: {"sales": float, ...}}."""
    items = await dodois_client.fetch_productivity_many(
        token, unit_uuids, from_dt, to_dt,
    )
    return {_normalize_uuid(it.get("unitId", "")): it for it in items}


async def _fetch_monthly_full(
    token: str, unit_uuids: list[str], from_dt: datetime, to_dt: datetime,
) -> dict[str, dict[str, float]]:
    """{uuid_norm: {total, delivery, restaurant}} через
    /finances/sales/units/monthly. total = row.sales, channels из
    salesBreakdown[].
    """
    rows = await dodois_client.fetch_finance_sales_monthly(
        token, unit_uuids,
        _fmt_date(from_dt.date()), _fmt_date(to_dt.date()),
    )
    by_uuid_total: dict[str, float] = defaultdict(float)
    by_uuid_ch_raw: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        uid = _normalize_uuid(row.get("unitId", ""))
        by_uuid_total[uid] += float(row.get("sales") or 0)
        for b in row.get("salesBreakdown") or []:
            ch = b.get("salesChannel") or ""
            by_uuid_ch_raw[uid][ch] += float(b.get("sales") or 0)
    out: dict[str, dict[str, float]] = {}
    all_uuids = set(by_uuid_total) | set(by_uuid_ch_raw)
    for uid in all_uuids:
        ch = _aggregate_channels(by_uuid_ch_raw.get(uid, {}))
        out[uid] = {
            "total": by_uuid_total.get(uid, 0.0),
            "delivery": ch["delivery"],
            "restaurant": ch["restaurant"],
        }
    return out


async def get_or_fetch_ly_full_month(
    session: AsyncSession,
    token: str,
    planfact_key_id: int,
    projects: list[tuple[str, str]],   # (project_id, unit_uuid)
    windows: BoardWindows,
) -> dict[str, dict[str, float]]:
    """Полный прошлогодний месяц для каждого проекта. БД-кэш immutable.
    Возвращает {project_id: {total, delivery, restaurant}}.

    Если хоть один проект не имеет записи — дёргаем Dodo IS за full month,
    записываем всё в БД, читаем обратно.
    """
    month_key = windows.last_year_month  # 'YYYY-MM'
    pids = [pid for pid, _ in projects]
    cached = await store.get_monthly_revenue_history(
        session, planfact_key_id, pids, month_key,
    )
    missing = [(pid, uuid) for pid, uuid in projects if pid not in cached]
    if not missing:
        # Все есть в БД.
        return {
            pid: {
                "total": float(cached[pid]["revenue_total"] or 0),
                "delivery": float(cached[pid]["revenue_delivery"] or 0),
                "restaurant": float(cached[pid]["revenue_restaurant"] or 0),
            }
            for pid in pids
        }

    # Берём LY_full из Dodo IS (одной партией всех юнитов).
    rows = await dodois_client.fetch_finance_sales_monthly(
        token, [u for _, u in missing],
        _fmt_date(windows.last_year_full_month.from_.date()),
        _fmt_date(windows.last_year_full_month.to.date()),
    )
    # Indeх: uuid_norm → агрегаты
    by_uuid_total: dict[str, float] = defaultdict(float)
    by_uuid_ch: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        uid = _normalize_uuid(row.get("unitId", ""))
        by_uuid_total[uid] += float(row.get("sales") or 0)
        for b in row.get("salesBreakdown") or []:
            ch = b.get("salesChannel") or ""
            by_uuid_ch[uid][ch] += float(b.get("sales") or 0)
    # Записываем в БД
    for pid, uuid in missing:
        uid = _normalize_uuid(uuid)
        ch = _aggregate_channels(by_uuid_ch[uid])
        await store.upsert_monthly_revenue(
            session, planfact_key_id, pid, month_key,
            revenue_total=by_uuid_total[uid],
            revenue_delivery=ch["delivery"],
            revenue_restaurant=ch["restaurant"],
        )
    await session.commit()
    # Перечитываем единым словарём
    cached = await store.get_monthly_revenue_history(
        session, planfact_key_id, pids, month_key,
    )
    return {
        pid: {
            "total": float(cached.get(pid, {}).get("revenue_total") or 0),
            "delivery": float(cached.get(pid, {}).get("revenue_delivery") or 0),
            "restaurant": float(cached.get(pid, {}).get("revenue_restaurant") or 0),
        }
        for pid in pids
    }


def _build_metric_block(
    current_total: float,
    baseline_total: float,
    current_channels: Optional[dict[str, float]],
    baseline_channels: Optional[dict[str, float]],
) -> dict:
    """Строим блок {value, baseline, delta_pct, channels: {…}}."""
    out: dict = {
        "value": current_total,
        "baseline": baseline_total,
        "delta_pct": _delta_pct(current_total, baseline_total),
    }
    if current_channels and baseline_channels:
        ch_out: dict = {}
        for k in ("delivery", "restaurant"):
            cur = current_channels.get(k, 0.0)
            base = baseline_channels.get(k, 0.0)
            ch_out[k] = {
                "value": cur,
                "baseline": base,
                "delta_pct": _delta_pct(cur, base),
            }
        out["channels"] = ch_out
    return out


async def build_board_payload(
    *,
    session: AsyncSession,
    token: str,
    planfact_key_id: int,
    projects: list[tuple[str, str, str]],  # (project_id, name, dodo_unit_uuid)
    now: Optional[datetime] = None,
) -> dict:
    """Главный orchestrator: собирает все окна и возвращает payload для UI."""
    windows = compute_board_windows(now)
    unit_uuids = [u for _, _, u in projects]
    # Маппинг uuid_norm → (pid, name)
    by_uuid = {_normalize_uuid(u): (pid, name) for pid, name, u in projects}

    # Имена пиццерий: читаем из dodois_units_cache. Если TTL >24h или
    # кэш пуст — обновляем из /auth/roles/units одним запросом и
    # перечитываем. Имена меняются крайне редко (новая точка — раз в
    # месяцы), на каждый /api/board дёргать API смысла нет.
    unit_names_by_uuid = await _get_or_refresh_unit_names(session, token)

    # 1) Параллельно: 2 окна accounting/sales (today + last_week — даёт
    # total + channels + unitName с минутной точностью) и 2 окна
    # /finances/sales/units/monthly (MTD + MTD_LFL — date-granular,
    # быстро через готовые агрегаты). LY_full отдельно через БД-кэш.
    # Плюс 4 типа stop-sales (активные стопы каналов/секторов/продуктов/
    # ингредиентов). Окно стопов: с вчера-00:00 до now — этого хватает,
    # чтобы поймать стопы, перешедшие через полночь и ещё активные.
    stops_window_from = (windows.today.from_ - timedelta(days=1))
    stops_window_to = windows.today.to

    # Ops окна: productivity требует hour-aligned, поэтому floor минут.
    # delivery/statistics и vouchers — любая точность.
    ops_today_to = windows.today.to.replace(minute=0, second=0, microsecond=0)
    ops_today_from = windows.today.from_
    ops_lw_from = windows.last_week.from_
    ops_lw_to = windows.last_week.to
    # Если ops_today_to == ops_today_from (например, время 00:30 — час ещё
    # не накопился), пропустим ops для today. Возвращаем индикатор пустого
    # окна через флаг empty_today.
    empty_ops_today = ops_today_to <= ops_today_from

    # ─── S21 (#3): DB-кэш immutable baseline-окон ───
    # last_week / mtd_lfl заканчиваются в прошлом и hour-aligned → срез за
    # конкретный window_to_key неизменен. Читаем кэш ДО gather (session
    # нельзя трогать конкурентно внутри gather), fetch'им только missing
    # проекты, пишем ПОСЛЕ gather. При полном хите HTTP не делается вовсе.
    projects_pu = [(pid, u) for pid, _, u in projects]
    all_pids = [pid for pid, _ in projects_pu]
    lw_to_key = windows.last_week.to.strftime("%Y-%m-%dT%H:00:00")
    lfl_to_key = windows.mtd_lfl.to.strftime("%Y-%m-%dT%H:00:00")

    sales_lw_cached = await store.get_window_cache_many(
        session, planfact_key_id, all_pids, "sales_lw", lw_to_key,
    )
    mtd_lfl_cached = await store.get_window_cache_many(
        session, planfact_key_id, all_pids, "monthly_lfl", lfl_to_key,
    )
    sales_lw_missing = [(pid, u) for pid, u in projects_pu if pid not in sales_lw_cached]
    mtd_lfl_missing = [(pid, u) for pid, u in projects_pu if pid not in mtd_lfl_cached]

    (
        sales_today, sales_lw, mtd_data, mtd_lfl_data,
        stops_channels_raw, stops_sectors_raw,
        stops_products_raw, stops_ingredients_raw,
        prod_today, prod_lw,
        deliv_today, deliv_lw,
        vouchers_today, vouchers_lw,
        handov_dlv_today, handov_dlv_lw,
        handov_din_today, handov_din_lw,
        couriers_on_shift_raw, couriers_orders_raw,
    ) = await asyncio.gather(
        # Sales и monthly — критичные, без safe-fetch. Иначе при пустом
        # ответе теряется unitName и пиццерии показываются как project_id.
        dodois_client.fetch_sales_by_channel(
            token, unit_uuids, windows.today.from_, windows.today.to,
        ),
        # last_week: только missing проекты (остальное из БД-кэша ниже)
        dodois_client.fetch_sales_by_channel(
            token, [u for _, u in sales_lw_missing],
            windows.last_week.from_, windows.last_week.to,
        ) if sales_lw_missing else asyncio.sleep(0, result={}),
        _fetch_monthly_full(
            token, unit_uuids, windows.mtd.from_, windows.mtd.to,
        ),
        # mtd_lfl: только missing проекты
        _fetch_monthly_full(
            token, [u for _, u in mtd_lfl_missing],
            windows.mtd_lfl.from_, windows.mtd_lfl.to,
        ) if mtd_lfl_missing else asyncio.sleep(0, result={}),
        _safe_fetch_stops(
            dodois_client.fetch_stop_sales_channels,
            token, unit_uuids, stops_window_from, stops_window_to,
            op_name="stop-channels",
        ),
        _safe_fetch_stops(
            dodois_client.fetch_stop_sales_sectors,
            token, unit_uuids, stops_window_from, stops_window_to,
            op_name="stop-sectors",
        ),
        _safe_fetch_stops(
            dodois_client.fetch_stop_sales_products,
            token, unit_uuids, stops_window_from, stops_window_to,
            op_name="stop-products",
        ),
        _safe_fetch_stops(
            dodois_client.fetch_stop_sales_ingredients,
            token, unit_uuids, stops_window_from, stops_window_to,
            op_name="stop-ingredients",
        ),
        # Productivity (₽/чел·ч, шт/чел·ч, заказ/курьер·ч, тепловая полка)
        _safe_fetch_ops(
            dodois_client.fetch_productivity_many,
            token, unit_uuids, ops_today_from, ops_today_to,
            op_name="prod-today",
            default=[],
        ) if not empty_ops_today else asyncio.sleep(0, result=[]),
        _safe_fetch_ops(
            dodois_client.fetch_productivity_many,
            token, unit_uuids, ops_lw_from, ops_lw_to,
            op_name="prod-lw",
            default=[],
        ),
        # Delivery stats (среднее доставки, готовка, тепловая полка, опоздания)
        _safe_fetch_ops(
            dodois_client.fetch_delivery_statistics,
            token, unit_uuids, windows.today.from_, windows.today.to,
            op_name="deliv-today",
            default=[],
        ),
        _safe_fetch_ops(
            dodois_client.fetch_delivery_statistics,
            token, unit_uuids, windows.last_week.from_, windows.last_week.to,
            op_name="deliv-lw",
            default=[],
        ),
        # Vouchers count (сертификаты опоздания)
        _safe_fetch_ops(
            dodois_client.fetch_late_delivery_vouchers_count,
            token, unit_uuids, windows.today.from_, windows.today.to,
            op_name="vou-today",
            default={},
        ),
        _safe_fetch_ops(
            dodois_client.fetch_late_delivery_vouchers_count,
            token, unit_uuids, windows.last_week.from_, windows.last_week.to,
            op_name="vou-lw",
            default={},
        ),
        # Готовка по каналам через /production/orders-handover-statistics.
        # BATCHED: один запрос на всю партию юнитов (до 30) с фильтром
        # salesChannels=Delivery/DineIn. 4 запроса всего, независимо от N.
        _safe_fetch_ops(
            functools.partial(dodois_client.fetch_orders_handover_statistics,
                              sales_channels="Delivery"),
            token, unit_uuids, windows.today.from_, windows.today.to,
            op_name="handover-deliv-today", default=[], budget_sec=20.0,
        ),
        _safe_fetch_ops(
            functools.partial(dodois_client.fetch_orders_handover_statistics,
                              sales_channels="Delivery"),
            token, unit_uuids, windows.last_week.from_, windows.last_week.to,
            op_name="handover-deliv-lw", default=[], budget_sec=20.0,
        ),
        _safe_fetch_ops(
            functools.partial(dodois_client.fetch_orders_handover_statistics,
                              sales_channels="DineIn"),
            token, unit_uuids, windows.today.from_, windows.today.to,
            op_name="handover-dinein-today", default=[], budget_sec=20.0,
        ),
        _safe_fetch_ops(
            functools.partial(dodois_client.fetch_orders_handover_statistics,
                              sales_channels="DineIn"),
            token, unit_uuids, windows.last_week.from_, windows.last_week.to,
            op_name="handover-dinein-lw", default=[], budget_sec=20.0,
        ),
        # Курьеры: на смене сейчас (snapshot) + заказы за последний час
        # для расчёта «свободно сейчас».
        _safe_fetch_ops(
            dodois_client.fetch_couriers_on_shift,
            token, unit_uuids,
            op_name="couriers-shift", default=[],
        ),
        _safe_fetch_ops(
            dodois_client.fetch_couriers_orders,
            token, unit_uuids,
            windows.today.from_,  # с начала текущего дня
            windows.now.replace(second=0, microsecond=0),
            op_name="couriers-orders-today", default=[],
        ),
    )


    # ─── S21 (#3): дописать свежие baseline-срезы в БД-кэш и смержить ───
    # sales_lw / mtd_lfl_data сейчас содержат ТОЛЬКО fetched missing-проекты.
    # Записываем их (immutable, insert-only), затем собираем полный
    # dict[uuid_norm] из кэша + свежих.
    _pending_writes: list[tuple] = []

    def _merge_window(cached, fetched, missing, metric_type, to_key, default):
        for pid, uuid in missing:
            un = _normalize_uuid(uuid)
            payload = fetched.get(un) or default()
            # внутри gather session не трогали — пишем здесь, последовательно
            _pending_writes.append((pid, metric_type, to_key, payload))
        full = {}
        for pid, uuid in projects_pu:
            un = _normalize_uuid(uuid)
            full[un] = cached[pid] if pid in cached else (fetched.get(un) or default())
        return full

    sales_lw = _merge_window(
        sales_lw_cached, sales_lw, sales_lw_missing, "sales_lw", lw_to_key,
        lambda: {"total": 0.0, "channels": {}, "name": None},
    )
    mtd_lfl_data = _merge_window(
        mtd_lfl_cached, mtd_lfl_data, mtd_lfl_missing, "monthly_lfl", lfl_to_key,
        lambda: {"total": 0.0, "delivery": 0.0, "restaurant": 0.0},
    )
    if _pending_writes:
        for pid, metric_type, to_key, payload in _pending_writes:
            await store.upsert_window_cache(
                session, planfact_key_id, pid, metric_type, to_key, payload,
            )
        await session.commit()

    # Индексы по uuid_norm для быстрого доступа в per-project цикле
    prod_today_by_uuid = _index_by_unit(prod_today)
    prod_lw_by_uuid = _index_by_unit(prod_lw)
    deliv_today_by_uuid = _index_by_unit(deliv_today)
    deliv_lw_by_uuid = _index_by_unit(deliv_lw)
    # vouchers_today/lw — уже {uuid_norm → count}, но fetch_late_delivery_vouchers_count
    # отдаёт нормализованный uuid. Если ключи raw — нормализуем.

    def _norm_count_map(m: dict) -> dict[str, int]:
        out: dict[str, int] = {}
        for k, v in (m or {}).items():
            out[_normalize_uuid(k)] = int(v or 0)
        return out

    vou_today_by_uuid = _norm_count_map(vouchers_today)
    vou_lw_by_uuid = _norm_count_map(vouchers_lw)

    # ─── Курьеры: total на смене + сколько в очереди ───
    # «В очереди» берём из numberOfCouriersInQueue последнего заказа (то
    # самое поле, что использует Dodo IS UI для индикатора «в очереди»).
    # Если последний заказ был >30 мин назад, считаем значение stale и
    # возвращаем None — UI покажет только total.
    on_shift_by_uuid: dict[str, set[str]] = defaultdict(set)
    for c in (couriers_on_shift_raw or []):
        uid = _normalize_uuid(c.get("unitId", ""))
        cid = c.get("id")
        if uid and cid:
            on_shift_by_uuid[uid].add(cid)

    # Парсим datetime последнего заказа per unit (по handedOverAtLocal).
    # Собираем latest_order per unit + его numberOfCouriersInQueue.
    latest_order_by_uuid: dict[str, dict] = {}
    for o in (couriers_orders_raw or []):
        uid = _normalize_uuid(o.get("unitId", ""))
        handed_str = o.get("handedOverToDeliveryAtLocal") or o.get("handedOverToDeliveryAt")
        if not uid or not handed_str:
            continue
        prev = latest_order_by_uuid.get(uid)
        if not prev or handed_str > (prev.get("handed_str") or ""):
            latest_order_by_uuid[uid] = {
                "handed_str": handed_str,
                "queue": o.get("numberOfCouriersInQueue"),
            }

    # Финальная map с total + in_queue per unit.
    # in_queue = numberOfCouriersInQueue из последнего заказа за окно
    # 4 часа. Без жёсткого freshness-gate: даже немного устаревшее
    # значение полезнее пустоты, и в часы пика обновляется живо.
    couriers_status_by_uuid: dict[str, dict] = {}
    for uid, on_shift_ids in on_shift_by_uuid.items():
        total = len(on_shift_ids)
        latest = latest_order_by_uuid.get(uid)
        in_queue: Optional[int] = None
        if latest:
            q = latest.get("queue")
            if isinstance(q, (int, float)):
                # Не превышаем total (защита от устаревшего значения,
                # когда курьеров стало меньше с момента того заказа).
                in_queue = min(int(q), total)
        couriers_status_by_uuid[uid] = {
            "total": total,
            "in_queue": in_queue,
        }

    # Готовка по каналам — индексы handover для today/LW в обоих каналах
    handov_dlv_today_by_uuid = _index_by_unit(handov_dlv_today)
    handov_dlv_lw_by_uuid = _index_by_unit(handov_dlv_lw)
    handov_din_today_by_uuid = _index_by_unit(handov_din_today)
    handov_din_lw_by_uuid = _index_by_unit(handov_din_lw)

    # LY_full_month через БД-кэш
    ly_full = await get_or_fetch_ly_full_month(
        session, token, planfact_key_id,
        [(pid, u) for pid, _, u in projects], windows,
    )

    # 2) Per-project payload
    project_blocks: list[dict] = []
    for pid, name, uuid in projects:
        uid = _normalize_uuid(uuid)

        # Имя: 1) из /auth/roles/units (всегда работает); 2) из
        # accounting/sales.unitName (если уже есть продажи); 3) fallback
        # на display_name из БД (project_id если и его нет).
        units_name = unit_names_by_uuid.get(uid)
        if units_name:
            name = units_name
        else:
            dodo_name = (sales_today.get(uid) or {}).get("name")
            if dodo_name:
                name = dodo_name

        # Day + last_week: total и channels из accounting/sales
        sd = sales_today.get(uid) or {"total": 0.0, "channels": {}}
        sl = sales_lw.get(uid) or {"total": 0.0, "channels": {}}
        d_total = float(sd.get("total") or 0)
        lw_total = float(sl.get("total") or 0)
        d_ch = _aggregate_channels(sd.get("channels") or {})
        lw_ch = _aggregate_channels(sl.get("channels") or {})

        # MTD + MTD_LFL: total и channels из finance/sales/units/monthly
        mtd = mtd_data.get(uid) or {"total": 0.0, "delivery": 0.0, "restaurant": 0.0}
        mtd_lfl = mtd_lfl_data.get(uid) or {"total": 0.0, "delivery": 0.0, "restaurant": 0.0}
        mtd_total = mtd["total"]
        mtd_lfl_total = mtd_lfl["total"]
        mtd_ch = {"delivery": mtd["delivery"], "restaurant": mtd["restaurant"]}
        mtd_lfl_ch = {"delivery": mtd_lfl["delivery"], "restaurant": mtd_lfl["restaurant"]}

        # LY full
        ly = ly_full.get(pid, {"total": 0.0, "delivery": 0.0, "restaurant": 0.0})

        # Прогноз
        forecast_value, method = forecast_month(
            mtd_total, mtd_lfl_total, ly.get("total", 0.0),
            fallback_days_in_month=(
                windows.last_year_full_month.to.date().day  # дней в этом месяце
            ),
            # MTD теперь по завершённым дням (до вчера) → делим на число
            # завершённых дней (день окончания окна), а не на сегодняшний.
            fallback_days_passed=windows.mtd.to.date().day,
        )

        stops = _build_stops_for_unit(
            uid_norm=uid,
            now_msk=windows.now,
            day_start=windows.today.from_,
            raw_channels=stops_channels_raw,
            raw_sectors=stops_sectors_raw,
            raw_products=stops_products_raw,
            raw_ingredients=stops_ingredients_raw,
        )

        # Ops — Кухня + Доставка с current/baseline
        pt = prod_today_by_uuid.get(uid) or {}
        pl = prod_lw_by_uuid.get(uid) or {}
        dt = deliv_today_by_uuid.get(uid) or {}
        dl = deliv_lw_by_uuid.get(uid) or {}
        hdt = handov_dlv_today_by_uuid.get(uid) or {}
        hdl = handov_dlv_lw_by_uuid.get(uid) or {}
        hit = handov_din_today_by_uuid.get(uid) or {}
        hil = handov_din_lw_by_uuid.get(uid) or {}

        def _g(d: dict, k: str) -> Optional[float]:
            v = d.get(k)
            return float(v) if isinstance(v, (int, float)) else None

        def _cook(d: dict) -> Optional[float]:
            """«Время приготовления» как в Dodo IS UI = avgOrderHandoverTime −
            avgHeatedShelfTime: полный путь от заказа до готовности (трекинг +
            готовка + сборка), БЕЗ ожидания выдачи на тепловой полке.
            avgCookingTime (только плита) занижал — не учитывал ожидание на
            трекинге ДО готовки. Сверено с Dodo IS UI: Кубинка доставка
            20:21 = handover 23:52 − полка 3:31.
            handover отсутствует → None (метрика недоступна)."""
            handover = d.get("avgOrderHandoverTime")
            if not isinstance(handover, (int, float)):
                return None
            shelf = d.get("avgHeatedShelfTime")
            shelf = float(shelf) if isinstance(shelf, (int, float)) else 0.0
            return float(handover) - shelf

        def _per_trip(d: dict) -> Optional[float]:
            """Заказов за поездку = deliveryOrdersCount / tripsCount
            (delivery/statistics). Та же формула, что на месячной странице
            (main.py: orders_per_trip). None при tripsCount=0."""
            orders = d.get("deliveryOrdersCount")
            trips = d.get("tripsCount")
            if not isinstance(orders, (int, float)) or not isinstance(trips, (int, float)):
                return None
            return float(orders) / float(trips) if trips else None

        ops = {
            "kitchen": {
                # ₽ на человеко-час: higher is better, не время
                "sales_per_hour": _ops_metric(
                    _g(pt, "salesPerLaborHour"), _g(pl, "salesPerLaborHour"),
                ),
                # шт на человеко-час: higher is better
                "products_per_hour": _ops_metric(
                    _g(pt, "productsPerLaborHour"), _g(pl, "productsPerLaborHour"),
                ),
                # Готовка · зал — handover − полка, канал DineIn
                "cooking_hall_sec": _ops_metric(
                    _cook(hit), _cook(hil),
                    lower_is_better=True, is_time=True,
                ),
                # Готовка · доставка — handover − полка, канал Delivery
                "cooking_delivery_sec": _ops_metric(
                    _cook(hdt), _cook(hdl),
                    lower_is_better=True, is_time=True,
                ),
                # Время на тепловой полке для ДОСТАВКИ (сек) — lower is better.
                # Берём из handover-statistics(Delivery), НЕ из
                # delivery/statistics: последний усредняет полку по всем
                # заказам и расходится с Dodo IS UI «Время ожидания доставки»
                # (Кубинка: delivery/stat 3:38 vs handover-Delivery 4:00 = UI
                # 03:59). hdt/hdl уже тянутся для метрики готовки — без
                # доп. запроса.
                "heated_shelf_sec": _ops_metric(
                    _g(hdt, "avgHeatedShelfTime"), _g(hdl, "avgHeatedShelfTime"),
                    lower_is_better=True, is_time=True,
                ),
            },
            "delivery": {
                # Заказов на курьера в час — higher is better
                "orders_per_courier_hour": _ops_metric(
                    _g(pt, "ordersPerCourierLabourHour"),
                    _g(pl, "ordersPerCourierLabourHour"),
                ),
                # Заказов за поездку — higher is better
                "orders_per_trip": _ops_metric(
                    _per_trip(dt), _per_trip(dl),
                ),
                # Среднее время доставки (сек) — lower is better, time
                "avg_delivery_sec": _ops_metric(
                    _g(dt, "avgDeliveryOrderFulfillmentTime"),
                    _g(dl, "avgDeliveryOrderFulfillmentTime"),
                    lower_is_better=True, is_time=True,
                ),
                # Сертификаты опоздания (шт) — lower is better
                "vouchers_count": _ops_metric(
                    float(vou_today_by_uuid.get(uid, 0)),
                    float(vou_lw_by_uuid.get(uid, 0)),
                    lower_is_better=True,
                ),
                # Курьеры — snapshot прямо сейчас. {total, in_queue}.
                # in_queue=None если последний заказ был >30 мин назад
                # (значение устарело — показываем только total).
                "couriers": couriers_status_by_uuid.get(uid, {
                    "total": 0, "in_queue": None,
                }),
            },
        }

        project_blocks.append({
            "id": pid,
            "name": name,
            "day": _build_metric_block(d_total, lw_total, d_ch, lw_ch),
            "month": _build_metric_block(mtd_total, mtd_lfl_total, mtd_ch, mtd_lfl_ch),
            "forecast": {
                "value": forecast_value,
                "ly_full": ly.get("total"),
                "delta_pct": _delta_pct(forecast_value, ly.get("total")) if forecast_value else None,
                "method": method,
            },
            "stops": stops,
            "ops": ops,
        })

    # 3) Сетевые тоталы — просто сумма по проектам
    def _sum(blocks: list[dict], key: str, sub: Optional[str] = None) -> float:
        out = 0.0
        for b in blocks:
            v = b.get(key) or {}
            if sub:
                v = (v.get("channels") or {}).get(sub) or {}
            x = v.get("value")
            if isinstance(x, (int, float)):
                out += x
        return out

    def _sum_baseline(blocks: list[dict], key: str, sub: Optional[str] = None) -> float:
        out = 0.0
        for b in blocks:
            v = b.get(key) or {}
            if sub:
                v = (v.get("channels") or {}).get(sub) or {}
            x = v.get("baseline")
            if isinstance(x, (int, float)):
                out += x
        return out

    net_day = _build_metric_block(
        _sum(project_blocks, "day"),
        _sum_baseline(project_blocks, "day"),
        {"delivery": _sum(project_blocks, "day", "delivery"),
         "restaurant": _sum(project_blocks, "day", "restaurant")},
        {"delivery": _sum_baseline(project_blocks, "day", "delivery"),
         "restaurant": _sum_baseline(project_blocks, "day", "restaurant")},
    )
    net_month = _build_metric_block(
        _sum(project_blocks, "month"),
        _sum_baseline(project_blocks, "month"),
        {"delivery": _sum(project_blocks, "month", "delivery"),
         "restaurant": _sum(project_blocks, "month", "restaurant")},
        {"delivery": _sum_baseline(project_blocks, "month", "delivery"),
         "restaurant": _sum_baseline(project_blocks, "month", "restaurant")},
    )

    # Прогноз сети — сумма прогнозов проектов; LY full network — сумма
    net_forecast_value = sum(
        (b.get("forecast") or {}).get("value") or 0 for b in project_blocks
    )
    net_ly_full = sum(
        (b.get("forecast") or {}).get("ly_full") or 0 for b in project_blocks
    )

    # Сортировка проектов: по day.delta_pct по возрастанию (худшие первыми);
    # пиццерии без данных в конец.
    def _sort_key(b: dict) -> tuple[int, float]:
        d = (b.get("day") or {}).get("delta_pct")
        if d is None:
            return (1, 0.0)
        return (0, d)

    project_blocks.sort(key=_sort_key)
    for i, b in enumerate(project_blocks, 1):
        b["rank"] = f"{i}/{len(project_blocks)}"

    # Сетевая сводка стопов — сумма count'ов по проектам.
    def _stops_counts(blocks: list[dict]) -> dict[str, int]:
        s = {"channels": 0, "sectors": 0, "products": 0, "ingredients": 0}
        for b in blocks:
            st = b.get("stops") or {}
            for k in s:
                s[k] += len(st.get(k) or [])
        return s

    return {
        "now_msk": windows.now.strftime("%Y-%m-%dT%H:%M:%S"),
        "to_hour": windows.to_hour,
        "today_date": _fmt_date(windows.today_date),
        "last_week_date": _fmt_date(windows.last_week_date),
        "month": windows.mtd.from_.strftime("%Y-%m"),
        "last_year_month": windows.last_year_month,
        "totals": {
            "day": net_day,
            "month": net_month,
            "forecast": {
                "value": net_forecast_value,
                "ly_full": net_ly_full,
                "delta_pct": _delta_pct(net_forecast_value, net_ly_full),
                "method": "lfl",
            },
            "stops": _stops_counts(project_blocks),
        },
        "projects": project_blocks,
    }
