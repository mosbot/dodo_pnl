"""S16.4: убираем whitelist KC должностей — KC по факту = всё кроме курьеров.

После эмпирической проверки: в Dodo IS есть 4 staffType — Operator,
KitchenMember, Cashier, Courier. Whitelist по positionName оказался
overkill: бизнес-правило простое — «всё кроме курьеров». Считаем напрямую
в коде через `shiftsDetailing[].staffType != "Courier"`.

Удаляем:
- planfact_keys.kc_kitchen_positions JSONB (введён в 0019)

Колонка ops_metrics.kc_live_pct остаётся — там лежит готовый расчёт.

Revision ID: 0020
Revises: 0019
"""
from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.planfact_keys
        DROP COLUMN kc_kitchen_positions
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.planfact_keys
        ADD COLUMN kc_kitchen_positions JSONB NOT NULL DEFAULT '[]'::jsonb
    """)
