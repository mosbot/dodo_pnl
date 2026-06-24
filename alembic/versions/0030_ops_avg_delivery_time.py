"""Среднее время доставки (avgDeliveryOrderFulfillmentTime) в ops_metrics.

ops_metrics.avg_delivery_fulfillment_sec — среднее время доставки за период
(от оформления заказа до вручения клиенту), в секундах. Источник —
/delivery/statistics.avgDeliveryOrderFulfillmentTime (тот же ответ, что уже
тянется для avg_cooking_time/avg_order_trip). На UI — формат mm:ss.
Пульс показывает live-значение того же поля; здесь — месячное (историческое).

Revision ID: 0030
Revises: 0029
"""
from alembic import op


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        ADD COLUMN avg_delivery_fulfillment_sec INTEGER
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE pnl_service.ops_metrics "
        "DROP COLUMN avg_delivery_fulfillment_sec"
    )
