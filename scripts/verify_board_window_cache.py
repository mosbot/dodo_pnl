"""Доказательство экономии #3: считаем вызовы baseline-fetch при двух
подряд сборках board для Xfood. 1й раз — кэш-промах (fetch), 2й — хит (0)."""
import asyncio
import time

from sqlalchemy import select
from app import board as board_module
from app import dodois_client
from app.auth.models import User
from app.auth.tokens import get_dodois_token
from app.db import get_session_factory
from app import store

counters = {"sales_by_channel": 0, "finance_monthly": 0}
_orig_sales = dodois_client.fetch_sales_by_channel
_orig_fin = dodois_client.fetch_finance_sales_monthly


async def _count_sales(token, uuids, *a, **k):
    if uuids:
        counters["sales_by_channel"] += 1
    return await _orig_sales(token, uuids, *a, **k)


async def _count_fin(token, uuids, *a, **k):
    if uuids:
        counters["finance_monthly"] += 1
    return await _orig_fin(token, uuids, *a, **k)


dodois_client.fetch_sales_by_channel = _count_sales
dodois_client.fetch_finance_sales_monthly = _count_fin
board_module.dodois_client.fetch_sales_by_channel = _count_sales
board_module.dodois_client.fetch_finance_sales_monthly = _count_fin


async def main():
    Sm = get_session_factory()
    async with Sm() as s:
        u = (await s.execute(
            select(User).where(User.planfact_key_id == 3).order_by(User.id)
        )).scalars().first()
        token = await get_dodois_token(s, u)
        cfg = await store.list_projects_config(s, 3)
        projects = [
            (pid, c.get("display_name") or pid, c["dodo_unit_uuid"])
            for pid, c in cfg.items()
            if c.get("is_active") and c.get("dodo_unit_uuid")
        ]
        print("Проектов:", len(projects))

        for run in (1, 2):
            counters["sales_by_channel"] = 0
            counters["finance_monthly"] = 0
            t0 = time.monotonic()
            payload = await board_module.build_board_payload(
                session=s, token=token, planfact_key_id=3, projects=projects,
            )
            dt = time.monotonic() - t0
            print("run %d: %.1fs | sales_by_channel вызовов=%d "
                  "(today всегда 1 + last_week) | finance_monthly=%d "
                  "(mtd всегда 1 + mtd_lfl) | day_baseline_net=%s"
                  % (run, dt, counters["sales_by_channel"], counters["finance_monthly"],
                     format(payload["totals"]["day"]["baseline"], ",.0f")))


asyncio.run(main())
