"""Рейтинг клиентов (средняя оценка заказов 0..5) в ops_metrics.

Источник — Customer Rating API Dodo IS:
  GET /customer-feedback/customer-ratings?units&from&to
Отдаёт per-unit avgDineInOrderRate / avgDeliveryOrderRate (0..5) + счётчики.
Месячное значение = взвешенное по числу оценок среднее зала и доставки.
Диапазонный средний (≤31 дн), «сегодня» недоступно → для текущего месяца
to обрезается до вчера.

Revision ID: 0033
Revises: 0032
"""
from alembic import op


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        ADD COLUMN customer_rating          DOUBLE PRECISION,
        ADD COLUMN customer_rating_dinein   DOUBLE PRECISION,
        ADD COLUMN customer_rating_delivery DOUBLE PRECISION
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE pnl_service.ops_metrics "
        "DROP COLUMN customer_rating, "
        "DROP COLUMN customer_rating_dinein, "
        "DROP COLUMN customer_rating_delivery"
    )
