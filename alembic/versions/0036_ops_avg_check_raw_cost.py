"""Средний чек (общий + по каналам) и Сырьё (food cost %) в ops_metrics.

Две новые Dodo IS-метрики Финансов:
  - avg_check / avg_check_delivery / avg_check_restaurant / avg_check_takeaway —
    средний чек за месяц. Общий = sales/ordersCount юнита; по каналам —
    агрегат salesBreakdown (Σsales/Σorders на канал) из того же месячного
    запроса /finances/sales/units/monthly (данные бесплатные).
  - raw_cost_pct — «Сырьё»: расход сырья от продаж (costWithVat, тип Sale из
    /accounting/stock-consumptions-by-period) / выручка юнита (с НДС) × 100.

Revision ID: 0036
Revises: 0035
"""
from alembic import op


revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        ADD COLUMN avg_check            DOUBLE PRECISION,
        ADD COLUMN avg_check_delivery   DOUBLE PRECISION,
        ADD COLUMN avg_check_restaurant DOUBLE PRECISION,
        ADD COLUMN avg_check_takeaway   DOUBLE PRECISION,
        ADD COLUMN raw_cost_pct         DOUBLE PRECISION
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        DROP COLUMN avg_check,
        DROP COLUMN avg_check_delivery,
        DROP COLUMN avg_check_restaurant,
        DROP COLUMN avg_check_takeaway,
        DROP COLUMN raw_cost_pct
    """)
