"""Lite-режим: immutable-кэш выручки закрытых месяцев (полная разбивка каналов).

lite_revenue_cache(planfact_key_id, project_id, month, payload JSONB, taken_at).
В Lite выручка/каналы тянулись из Dodo IS на КАЖДЫЙ запрос, в т.ч. для закрытых
месяцев (в полном P&L закрытые месяцы лежат в cache_history, в Lite такого слоя
не было). Закрытый полный месяц immutable → пишем один раз, дальше читаем из БД.
payload = {"total": float, "channels": {delivery, restaurant, takeaway, other}}.

Деплой безопасен: пустая таблица = поведение как раньше (всё live, пишется при
первом обращении к закрытому месяцу). Текущий/частичный период не кэшируется.

Revision ID: 0031
Revises: 0030
"""
from alembic import op


revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pnl_service.lite_revenue_cache (
            planfact_key_id  BIGINT NOT NULL
                REFERENCES pnl_service.planfact_keys(id) ON DELETE CASCADE,
            project_id       VARCHAR(64)  NOT NULL,
            month            VARCHAR(7)   NOT NULL,
            payload          JSONB        NOT NULL,
            taken_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (planfact_key_id, project_id, month)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE pnl_service.lite_revenue_cache")
