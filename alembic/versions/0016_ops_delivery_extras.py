"""S16: пять дополнительных ops-метрик из /delivery/statistics.

Все данные приходят из ручки `GET /delivery/statistics`, которую
`_run_ops_sync` уже дёргает. Новые колонки:

- `orders_per_trip`           — deliveryOrdersCount / tripsCount
- `avg_order_trip_time_min`   — avgOrderTripTime (сек) / 60
- `avg_cooking_time_min`      — avgCookingTime    (сек) / 60
- `courier_utilization_pct`   — tripsDuration / couriersShiftsDuration × 100
- `courier_app_share_pct`     — ordersWithCourierAppCount / deliveryOrdersCount × 100

Храним AOT и cook time сразу в МИНУТАХ (а не секундах), чтобы на UI
не делать арифметику и форматировать одним `digits=1`.

Revision ID: 0016
Revises: 0015
"""
from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        ADD COLUMN orders_per_trip          REAL,
        ADD COLUMN avg_order_trip_time_min  REAL,
        ADD COLUMN avg_cooking_time_min     REAL,
        ADD COLUMN courier_utilization_pct  REAL,
        ADD COLUMN courier_app_share_pct    REAL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        DROP COLUMN courier_app_share_pct,
        DROP COLUMN courier_utilization_pct,
        DROP COLUMN avg_cooking_time_min,
        DROP COLUMN avg_order_trip_time_min,
        DROP COLUMN orders_per_trip
    """)
