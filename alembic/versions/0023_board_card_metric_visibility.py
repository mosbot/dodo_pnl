"""S19: board_card_metric_visibility — настройка видимости ops-метрик
на rich-card странице /board.

Метрики на карточке /board hardcoded в коде (8 штук: ₽/чел·ч,
шт/чел·ч, две Готовки, заказ/курьер, среднее доставки, полка,
сертификаты). Эта таблица позволяет per-planfact-ключ отключить
ненужные метрики через UI на /settings.

PK: (planfact_key_id, metric_code). Записываем только когда юзер
отключил метрику (is_visible=false). Запись отсутствует → видна
(default). Так миграция БЕЗ seed данных — все метрики видны
сразу после деплоя.

Revision ID: 0023
Revises: 0022
"""
from alembic import op


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pnl_service.board_card_metric_visibility (
            planfact_key_id  BIGINT NOT NULL
                REFERENCES pnl_service.planfact_keys(id) ON DELETE CASCADE,
            metric_code      VARCHAR(64) NOT NULL,
            is_visible       BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (planfact_key_id, metric_code)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE pnl_service.board_card_metric_visibility")
