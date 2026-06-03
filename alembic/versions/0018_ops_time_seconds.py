"""S16.2: ops-метрики времени храним в секундах (а не минутах с дробной частью).

Контекст: на UI хотим формат `mm:ss` (например 14:42). Минуты с дробной
частью (14.7) теряли точность секунд и плохо смотрелись. Натуральный
формат от Dodo IS — секунды, integer. Возвращаемся к нему.

Изменения колонок ops_metrics:
- avg_order_trip_time_min          → avg_order_trip_time_sec        (REAL → INT)
- avg_cooking_time_delivery_min    → avg_cooking_time_delivery_sec  (REAL → INT)
- avg_cooking_time_restaurant_min  → avg_cooking_time_restaurant_sec (REAL → INT)

Данные за уже синкнутые месяцы (если были): умножаем на 60 при rename,
INTEGER принимает результат.

Revision ID: 0018
Revises: 0017
"""
from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER COLUMN TYPE INTEGER USING (...) — конвертирует REAL → INT
    # с умножением на 60 (минуты с дробной → секунды).
    for old, new in [
        ("avg_order_trip_time_min", "avg_order_trip_time_sec"),
        ("avg_cooking_time_delivery_min", "avg_cooking_time_delivery_sec"),
        ("avg_cooking_time_restaurant_min", "avg_cooking_time_restaurant_sec"),
    ]:
        op.execute(f"""
            ALTER TABLE pnl_service.ops_metrics
            ALTER COLUMN {old} TYPE INTEGER
            USING (ROUND({old} * 60))::INTEGER
        """)
        op.execute(f"""
            ALTER TABLE pnl_service.ops_metrics
            RENAME COLUMN {old} TO {new}
        """)


def downgrade() -> None:
    for old, new in [
        ("avg_cooking_time_restaurant_sec", "avg_cooking_time_restaurant_min"),
        ("avg_cooking_time_delivery_sec", "avg_cooking_time_delivery_min"),
        ("avg_order_trip_time_sec", "avg_order_trip_time_min"),
    ]:
        op.execute(f"""
            ALTER TABLE pnl_service.ops_metrics
            RENAME COLUMN {old} TO {new}
        """)
        op.execute(f"""
            ALTER TABLE pnl_service.ops_metrics
            ALTER COLUMN {new} TYPE REAL
            USING ({new}::REAL / 60.0)
        """)
