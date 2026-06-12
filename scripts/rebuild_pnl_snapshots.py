"""Прогрев снэпшотов закрытых месяцев через текущий pnl_source (v2).
Проверяет, что cache miss пересобирается v2-путём и пишется в cache_history."""
import asyncio
import time
from calendar import monthrange

from sqlalchemy import select, text
from app import main as app_main
from app.auth.models import User
from app.auth.tokens import get_planfact_key
from app.db import get_session_factory
from app.planfact import get_planfact_client

# Закрытые месяцы для прогрева. Текущий (2026-06) и май в live-окне —
# их трогать не нужно, они и так live. Берём всё до апреля включительно.
PERIODS = [
    "2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06",
    "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04",
]


async def warm(s, key_id, period):
    u = (await s.execute(
        select(User).where(User.planfact_key_id == key_id).order_by(User.id)
    )).scalars().first()
    key = await get_planfact_key(s, u)
    pf = get_planfact_client(u.id, key)
    y, m = map(int, period.split("-"))
    ds = "%s-01" % period
    de = "%s-%02d" % (period, monthrange(y, m)[1])
    t0 = time.monotonic()
    res = await app_main._build_pnl_for_period(
        session=s, user=u, pf=pf,
        date_start=ds, date_end=de, period_month=period,
        project_filter=None, method="accrual",
    )
    dt = time.monotonic() - t0
    totals = {ln["code"]: ln["total"]["amount"] for ln in res["lines"]}
    stats = res.get("stats") or {}
    src = stats.get("source") or stats.get("cache") or "raw-live"
    rev = totals.get("REVENUE", 0)
    net = totals.get("NET_PROFIT", 0)
    print("  key=%d %s: %.1fs source=%-6s REVENUE=%15s NET=%15s"
          % (key_id, period, dt, src, format(rev, ",.0f"), format(net, ",.0f")))


async def main():
    Sm = get_session_factory()
    async with Sm() as s:
        for key_id in (1, 3):
            print("=== key %d ===" % key_id)
            for p in PERIODS:
                try:
                    await warm(s, key_id, p)
                except Exception as e:
                    print("  key=%d %s: ERROR %s: %s" % (key_id, p, type(e).__name__, e))
        rows = (await s.execute(text(
            "SELECT planfact_key_id, count(*) FROM pnl_service.cache_history "
            "GROUP BY 1 ORDER BY 1"
        ))).all()
        print("\nСнэпшотов в cache_history после прогрева:", rows)


asyncio.run(main())
