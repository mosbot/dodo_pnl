"""Валидация миграции S20: raw vs v2 P&L через РЕАЛЬНЫЕ кодовые пути.

Прогоняет для каждого PF-ключа и метода:
  raw: main._fetch_period + pnl.build_pnl (как прод сейчас)
  v2:  main._build_pnl_v2_result          (как прод после переключения)
и сравнивает построчно lines, category_breakdown и revenue_by_channel.

Запуск на VPS (read-only, в БД ничего не пишет):
  cd /home/claude/pnl-service && PYTHONPATH=. .venv/bin/python scripts/validate_v2_pnl.py 2026-05
"""
import asyncio
import sys
import time

from sqlalchemy import select

from app import main as app_main
from app import pnl as pnl_module
from app.auth.models import PlanfactKey, User
from app.auth.tokens import get_planfact_key
from app.db import get_session_factory
from app.planfact import get_planfact_client

TOL = 0.01  # копейка


def month_range(period: str) -> tuple[str, str]:
    from calendar import monthrange
    y, m = map(int, period.split("-"))
    return f"{period}-01", f"{period}-{monthrange(y, m)[1]:02d}"


async def compare_for_key(session, key: PlanfactKey, period: str, method: str) -> bool:
    ds, de = month_range(period)
    user = (await session.execute(
        select(User)
        .where(User.planfact_key_id == key.id, User.visibility_level == 100)
        .order_by(User.id)
    )).scalars().first()
    if user is None:
        print(f"  [skip] нет юзера с visibility=100 на ключе {key.name}")
        return True
    api_key = await get_planfact_key(session, user)
    pf = get_planfact_client(user.id, api_key)

    common = dict(
        session=session, date_start=ds, date_end=de,
        period_month=period, project_filter=None, method=method,
    )

    t0 = time.monotonic()
    projects, categories, operations = await app_main._fetch_period(
        pf, ds, de, None, method=method,
    )
    raw = await pnl_module.build_pnl(
        owner_id=user.id, planfact_key_id=key.id,
        categories=categories, operations=operations, projects=projects,
        user_visibility_level=100, **common,
    )
    t_raw = time.monotonic() - t0

    pf.invalidate_cache()  # честное время v2 без нашего кэша
    t0 = time.monotonic()
    v2 = await app_main._build_pnl_v2_result(user=user, pf=pf, **common)
    t_v2 = time.monotonic() - t0
    if v2 is None:
        print(f"  [FAIL] v2 вернул None (fallback) — см. лог")
        return False

    ok = True

    def totals_of(res):
        return {str(l["code"]): float((l.get("total") or {}).get("amount") or 0)
                for l in res.get("lines") or []}

    rt, vt = totals_of(raw), totals_of(v2)
    for code in sorted(set(rt) | set(vt)):
        a, b = rt.get(code, 0.0), vt.get(code, 0.0)
        if abs(a - b) > TOL:
            print(f"  [DIFF] line {code}: raw={a:,.2f} v2={b:,.2f} Δ={a-b:+,.2f}")
            ok = False

    # category_breakdown: сумма по (pid, cid)
    def cb_map(res):
        out = {}
        for cb in res.get("category_breakdown") or []:
            out[(cb["project_id"], cb["category_id"])] = float(cb["amount"] or 0)
        return out

    rc, vc = cb_map(raw), cb_map(v2)
    cb_diffs = [k for k in set(rc) | set(vc) if abs(rc.get(k, 0) - vc.get(k, 0)) > TOL]
    if cb_diffs:
        ok = False
        print(f"  [DIFF] category_breakdown: {len(cb_diffs)} расхождений, примеры:")
        for k in cb_diffs[:5]:
            print(f"     {k}: raw={rc.get(k, 0):,.2f} v2={vc.get(k, 0):,.2f}")

    # revenue_by_channel
    rrc, vrc = raw.get("revenue_by_channel") or {}, v2.get("revenue_by_channel") or {}
    for pid in set(rrc) | set(vrc):
        for ch in ("delivery", "restaurant", "takeaway", "other"):
            a = float((rrc.get(pid) or {}).get(ch) or 0)
            b = float((vrc.get(pid) or {}).get(ch) or 0)
            if abs(a - b) > TOL:
                print(f"  [DIFF] channel {pid}/{ch}: raw={a:,.2f} v2={b:,.2f}")
                ok = False

    # template_lines: итоговые суммы узлов шаблона
    def tpl(res):
        return {str(n.get("id")): float((n.get("total") or {}).get("amount") or 0)
                for n in res.get("template_lines") or []}

    rtp, vtp = tpl(raw), tpl(v2)
    tpl_diffs = [k for k in set(rtp) | set(vtp) if abs(rtp.get(k, 0) - vtp.get(k, 0)) > TOL]
    if tpl_diffs:
        ok = False
        print(f"  [DIFF] template_lines: {len(tpl_diffs)} узлов расходятся")

    rev = rt.get("REVENUE", 0)
    npf = rt.get("NET_PROFIT", 0)
    print(f"  [{'OK  ' if ok else 'FAIL'}] {key.name} {period} {method}: "
          f"REVENUE={rev:,.0f} NET_PROFIT={npf:,.0f} | "
          f"lines={len(rt)} cb={len(rc)} | raw {t_raw:.1f}s vs v2 {t_v2:.1f}s")
    return ok


async def main():
    period = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
    Sm = get_session_factory()
    all_ok = True
    async with Sm() as session:
        keys = (await session.execute(select(PlanfactKey))).scalars().all()
        for key in keys:
            for method in ("accrual", "cash"):
                print(f"\n=== {key.name} / {period} / {method} ===")
                try:
                    ok = await compare_for_key(session, key, period, method)
                except Exception as e:
                    print(f"  [ERROR] {type(e).__name__}: {e}")
                    ok = False
                all_ok = all_ok and ok
    print("\n" + ("ВСЁ СХОДИТСЯ ✓" if all_ok else "ЕСТЬ РАСХОЖДЕНИЯ ✗"))
    sys.exit(0 if all_ok else 1)


asyncio.run(main())
