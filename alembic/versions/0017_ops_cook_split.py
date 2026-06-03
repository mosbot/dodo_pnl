"""S16.1: разделить avg_cooking_time на delivery/restaurant + убрать courier_app_share.

Контекст:
- COURIER_APP_SHARE (доля доставок через курьерское app) оказалась не нужна
  бизнесу — выпиливаем колонку и метрику.
- Время готовки имеет смысл смотреть по каналам отдельно: на доставке оно
  обычно длиннее (упаковка, ожидание курьера на полке), на dine-in быстрее.
  Источники:
    * delivery → /delivery/statistics.avgCookingTime (уже тянем);
    * restaurant → /production/orders-handover-statistics?salesChannels=Dine-in.

Колоночные изменения:
- DROP `courier_app_share_pct`
- RENAME `avg_cooking_time_min` → `avg_cooking_time_delivery_min`
- ADD  `avg_cooking_time_restaurant_min`

Данные за прошлые месяцы в `avg_cooking_time_min` были недавно (5 минут назад)
залиты миграцией 0016 на NULL; те, что юзер успел синкнуть — это delivery
по смыслу (брали из /delivery/statistics), так что rename корректен.

Revision ID: 0017
Revises: 0016
"""
from alembic import op


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        DROP COLUMN courier_app_share_pct
    """)
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        RENAME COLUMN avg_cooking_time_min TO avg_cooking_time_delivery_min
    """)
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        ADD COLUMN avg_cooking_time_restaurant_min REAL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        DROP COLUMN avg_cooking_time_restaurant_min
    """)
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        RENAME COLUMN avg_cooking_time_delivery_min TO avg_cooking_time_min
    """)
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        ADD COLUMN courier_app_share_pct REAL
    """)
