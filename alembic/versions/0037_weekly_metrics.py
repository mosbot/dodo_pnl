"""Планёрка (Фаза 1): immutable-кэш недельных метрик.

weekly_metrics(planfact_key_id, project_id, iso_week 'YYYY-Www', payload JSONB,
computed_at). Закрытые ISO-недели immutable: считаются из Dodo IS
(finances/sales/units/daily) один раз, дальше читаются из БД. Текущая неделя
не кэшируется (live). payload: rev/orders по каналам + mtd + ly (LFL).

Revision ID: 0037
Revises: 0036
"""
from alembic import op


revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pnl_service.weekly_metrics (
            planfact_key_id  BIGINT NOT NULL
                REFERENCES pnl_service.planfact_keys(id) ON DELETE CASCADE,
            project_id       VARCHAR(64)  NOT NULL,
            iso_week         VARCHAR(8)   NOT NULL,
            payload          JSONB        NOT NULL,
            computed_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (planfact_key_id, project_id, iso_week)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pnl_service.weekly_metrics")
