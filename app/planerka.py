"""Планёрка (Фаза 1) — недельные метрики для управляющих.

GET /api/planerka: сетка ISO-недель × метрики выручки/чеков (по каналам) +
LFL (та же ISO-неделя год назад) + MTD-накопления. Источник — ТОЛЬКО Dodo IS
(finances/sales/units/daily), работает для любого тенанта (Lite включительно).

Кэш: weekly_metrics (закрытые недели immutable, пишем один раз). Текущая
неделя всегда live. Дневные фетчи дробятся по календарным месяцам (лимит окна
эндпоинта ~62 дня) и идут батчами ≤30 юнитов.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import dodois_client, store
from .auth.dependencies import require_user
from .auth.models import User
from .auth.tokens import get_dodois_token
from .db import get_session

log = logging.getLogger("uvicorn.error")
_ANCHOR_RE = re.compile(r"^\d{4}-W\d{2}$")  # аудит P11

router = APIRouter()

_CH_MAP = {"Delivery": "delivery", "Dine-in": "restaurant", "Takeaway": "takeaway"}


# ---------- недельная математика ----------

def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_range(key: str) -> tuple[date, date]:
    y, w = int(key[:4]), int(key[6:])
    mon = date.fromisocalendar(y, w, 1)
    return mon, mon + timedelta(days=6)


def prev_week(key: str) -> str:
    mon, _ = week_range(key)
    return week_key(mon - timedelta(days=7))


def ly_week(key: str) -> str:
    """Та же ISO-неделя год назад. W53 в невисокосном ISO-году → W52."""
    y, w = int(key[:4]), int(key[6:])
    try:
        date.fromisocalendar(y - 1, w, 1)
        return f"{y - 1}-W{w:02d}"
    except ValueError:
        return f"{y - 1}-W52"


def mtd_start(sunday: date) -> date:
    """Начало накопления: 1-е число месяца, в котором лежит КОНЕЦ недели."""
    return sunday.replace(day=1)


# ---------- агрегация дневных данных ----------

def _norm(u: str) -> str:
    return (u or "").lower().replace("-", "")


class _DayIndex:
    """(uuid_norm, iso_date) -> {"sales","orders","ch":{delivery/restaurant/
    takeaway: {"sales","orders"}}}. Заполняется из finance-daily."""

    def __init__(self) -> None:
        self.days: dict[tuple[str, str], dict] = {}
        self.fetched_spans: list[tuple[date, date]] = []

    def covers(self, d0: date, d1: date) -> bool:
        return any(a <= d0 and d1 <= b for a, b in self.fetched_spans)

    def add_rows(self, rows: list[dict]) -> None:
        for r in rows:
            uid = _norm(r.get("unitId"))
            dt = (r.get("date") or "")[:10]
            if not uid or not dt:
                continue
            e = {"sales": float(r.get("sales") or 0),
                 "orders": int(r.get("ordersCount") or 0),
                 "ch": {k: {"sales": 0.0, "orders": 0} for k in _CH_MAP.values()}}
            for b in r.get("salesBreakdown") or []:
                ch = _CH_MAP.get(b.get("salesChannel") or "")
                if not ch:
                    continue
                e["ch"][ch]["sales"] += float(b.get("sales") or 0)
                e["ch"][ch]["orders"] += int(b.get("ordersCount") or 0)
            self.days[(uid, dt)] = e

    def agg(self, uuid_norm: str, d0: date, d1: date) -> dict:
        out = {"sales": 0.0, "orders": 0,
               "ch": {k: {"sales": 0.0, "orders": 0} for k in _CH_MAP.values()}}
        d = d0
        while d <= d1:
            e = self.days.get((uuid_norm, d.isoformat()))
            if e:
                out["sales"] += e["sales"]
                out["orders"] += e["orders"]
                for k in out["ch"]:
                    out["ch"][k]["sales"] += e["ch"][k]["sales"]
                    out["ch"][k]["orders"] += e["ch"][k]["orders"]
            d += timedelta(days=1)
        return out


def _split_chunks(d0: date, d1: date, max_days: int = 10) -> list[tuple[date, date]]:
    """Куски ≤10 дней: daily-эндпоинт отдаёт 400 DateOutOfRange на окно >10
    дней («The time period cannot exceed 10 days», проверено 2026-07-30).
    Куски выравниваем по декадам месяца (1-10/11-20/21-конец), чтобы окна
    разных недель переиспользовали одни и те же куски (меньше запросов +
    дедуп по ключу куска)."""
    out = []
    cur = d0
    while cur <= d1:
        if cur.day <= 10:
            c_end = cur.replace(day=10)
        elif cur.day <= 20:
            c_end = cur.replace(day=20)
        else:
            c_end = (date(cur.year, 12, 31) if cur.month == 12
                     else date(cur.year, cur.month + 1, 1) - timedelta(days=1))
        out.append((cur, min(c_end, d1)))
        cur = c_end + timedelta(days=1)
    return out


async def _fetch_spans(
    idx: _DayIndex, token: str, uuids: list[str], spans: list[tuple[date, date]],
) -> None:
    """Стягивает недостающие диапазоны (помесячно, параллельно)."""
    need: list[tuple[date, date]] = []
    for d0, d1 in spans:
        if not idx.covers(d0, d1):
            need.extend(_split_chunks(d0, d1))
    # dedupe кусков
    uniq = sorted({(a.isoformat(), b.isoformat()) for a, b in need})
    if not uniq:
        return

    async def _one(a: str, b: str) -> list[dict]:
        return await dodois_client.fetch_finance_sales_daily(token, uuids, a, b)

    results = await asyncio.gather(*(_one(a, b) for a, b in uniq))
    for (a, b), rows in zip(uniq, results):
        idx.add_rows(rows)
        idx.fetched_spans.append((date.fromisoformat(a), date.fromisoformat(b)))


# ---------- payload недели ----------

def _pack(cur: dict, mtd: dict, ly: dict, ly_mtd: dict) -> dict:
    def slim(a: dict) -> dict:
        return {
            "sales": round(a["sales"], 2), "orders": a["orders"],
            "ch": {k: {"sales": round(v["sales"], 2), "orders": v["orders"]}
                   for k, v in a["ch"].items()},
        }
    return {"cur": slim(cur), "mtd": slim(mtd),
            "ly": slim(ly), "ly_mtd": slim(ly_mtd)}


# ---------- endpoint ----------

@router.get("/api/planerka")
async def get_planerka(
    weeks: int = Query(8, ge=2, le=26),
    anchor: str | None = Query(None, description="'YYYY-Www' — последняя неделя окна"),
    project_ids: list[str] | None = Query(None),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    from . import main as _main  # поздний импорт (капабилити), без цикла
    await _main._require_capability(session, user, "finance")
    pf_key_id = user.planfact_key_id
    if not pf_key_id:
        raise HTTPException(400, "Нет ключа тенанта")

    cfg = await store.list_projects_config(session, pf_key_id)
    # Аудит 2026-09-03 P11: учитываем персональные скрытые проекты юзера — как
    # в _resolve_project_filter Финансов (раньше Планёрка их показывала).
    hidden = await store.get_user_hidden_projects(session, user.id)
    allowed = set(project_ids) if project_ids else None
    units: list[tuple[str, str, str]] = []  # (pid, uuid, name)
    for pid, c in cfg.items():
        if not c.get("dodo_unit_uuid") or not c.get("is_active", True):
            continue
        if pid in hidden:
            continue
        if allowed is not None and pid not in allowed:
            continue
        units.append((pid, c["dodo_unit_uuid"], c.get("display_name") or pid))
    if not units:
        return {"weeks": [], "projects": [], "data": {}}

    today = date.today()
    # P11: anchor валидируем — иначе произвольная строка даёт 500 (ValueError).
    if anchor and not _ANCHOR_RE.match(anchor):
        raise HTTPException(400, "anchor должен быть в формате 'YYYY-Www'")
    anchor_key = anchor or week_key(today)
    keys: list[str] = []
    k = anchor_key
    for _ in range(weeks):
        keys.append(k)
        k = prev_week(k)
    keys.reverse()

    week_meta = []
    for k in keys:
        d0, d1 = week_range(k)
        week_meta.append({
            "key": k, "start": d0.isoformat(), "end": d1.isoformat(),
            "closed": d1 < today,
        })

    # --- кэш закрытых недель ---
    rows = (await session.execute(text("""
        SELECT project_id, iso_week, payload
        FROM pnl_service.weekly_metrics
        WHERE planfact_key_id = :k AND iso_week = ANY(:wk)
    """), {"k": pf_key_id, "wk": keys})).all()
    cache: dict[tuple[str, str], dict] = {
        (r[0], r[1]): (r[2] if isinstance(r[2], dict) else json.loads(r[2]))
        for r in rows
    }

    # --- чего не хватает ---
    missing_weeks: set[str] = set()
    for wm in week_meta:
        if not wm["closed"]:
            missing_weeks.add(wm["key"])  # текущая — всегда live
            continue
        if any((pid, wm["key"]) not in cache for pid, _, _ in units):
            missing_weeks.add(wm["key"])

    data: dict[str, dict[str, dict]] = defaultdict(dict)
    for (pid, wk), payload in cache.items():
        data[pid][wk] = payload

    if missing_weeks:
        token = await get_dodois_token(session, user)
        uuids = [u for _, u, _ in units]
        idx = _DayIndex()
        spans: list[tuple[date, date]] = []
        for wk in missing_weeks:
            d0, d1 = week_range(wk)
            # ВАЖНО: неделя может пересекать границу месяца — mtd_start(конец)
            # тогда ПОЗЖЕ понедельника. Берём min, чтобы покрыть и неделю
            # целиком, и MTD-хвост (был баг: терялись дни прошлого месяца).
            spans.append((min(d0, mtd_start(d1)), min(d1, today)))
            l0, l1 = week_range(ly_week(wk))
            spans.append((min(l0, mtd_start(l1)), l1))
        await _fetch_spans(idx, token, uuids, spans)

        to_upsert: list[tuple[str, str, str]] = []
        for wk in missing_weeks:
            d0, d1 = week_range(wk)
            lyk = ly_week(wk)
            l0, l1 = week_range(lyk)
            eff_d1 = min(d1, today)  # текущая неделя — по сегодня
            # LFL текущей (частичной) недели — то же число дней LY-недели
            eff_l1 = l0 + (eff_d1 - d0)
            for pid, uu, _ in units:
                un = _norm(uu)
                payload = _pack(
                    cur=idx.agg(un, d0, eff_d1),
                    mtd=idx.agg(un, mtd_start(d1), eff_d1),
                    ly=idx.agg(un, l0, eff_l1),
                    ly_mtd=idx.agg(un, mtd_start(l1), eff_l1),
                )
                data[pid][wk] = payload
                if d1 < today:
                    to_upsert.append((pid, wk, json.dumps(payload)))
        for pid, wk, pj in to_upsert:
            await session.execute(text("""
                INSERT INTO pnl_service.weekly_metrics
                    (planfact_key_id, project_id, iso_week, payload)
                VALUES (:k, :p, :w, CAST(:j AS jsonb))
                ON CONFLICT (planfact_key_id, project_id, iso_week)
                DO UPDATE SET payload = EXCLUDED.payload, computed_at = now()
            """), {"k": pf_key_id, "p": pid, "w": wk, "j": pj})
        if to_upsert:
            await session.commit()

    return {
        "weeks": week_meta,
        "projects": [{"id": pid, "name": name} for pid, _, name in units],
        "data": data,
    }
