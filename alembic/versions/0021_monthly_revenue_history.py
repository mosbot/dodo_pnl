"""S17: таблица monthly_revenue_history — кэш закрытых месяцев из Dodo IS.

Используется для расчёта прогноза текущего месяца (LFL-projection): нужен
полный объём выручки прошлогоднего того же месяца. Чтобы не дёргать Dodo IS
на каждый refresh, пишем закрытые месяцы в БД один раз и навсегда — данные
immutable.

PK: (planfact_key_id, project_id, month).
month: 'YYYY-MM' (string-7).

Сюда же пишем разбивку по каналам — пригодится для прогноза по каналам
в будущих итерациях.

Revision ID: 0021
Revises: 0020
"""
from alembic import op


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pnl_service.monthly_revenue_history (
            planfact_key_id    BIGINT NOT NULL
                REFERENCES pnl_service.planfact_keys(id) ON DELETE CASCADE,
            project_id         VARCHAR(64) NOT NULL,
            month              VARCHAR(7) NOT NULL,
            revenue_total      REAL,
            revenue_delivery   REAL,
            revenue_restaurant REAL,
            taken_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (planfact_key_id, project_id, month)
        )
    """)
    op.execute("""
        CREATE INDEX ix_monthly_revenue_pfkey_month
            ON pnl_service.monthly_revenue_history (planfact_key_id, month)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE pnl_service.monthly_revenue_history")
