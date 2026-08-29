"""Дневной лог активности пользователей.

user_activity_days(user_id, day MSK, requests, first_seen_at, last_seen_at).
Пишется из auth-слоя (см. app/auth/activity.py) с in-memory дебаунсом
(~10 мин на пользователя), поэтому requests — приблизительный счётчик
авторизованных запросов, а не точный access-log. Нужна потому, что
sessions.last_seen_at хранит только ПОСЛЕДНИЙ момент активности сессии —
история заходов долгоживущих сессий (TTL 30 дней) иначе невидима.

Revision ID: 0038
Revises: 0037
"""
from alembic import op


revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pnl_service.user_activity_days (
            user_id        BIGINT NOT NULL
                REFERENCES pnl_service.users(id) ON DELETE CASCADE,
            day            DATE         NOT NULL,
            requests       INTEGER      NOT NULL DEFAULT 0,
            first_seen_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            last_seen_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, day)
        )
    """)
    op.execute("""
        CREATE INDEX ix_user_activity_days_day
            ON pnl_service.user_activity_days (day)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pnl_service.user_activity_days")
