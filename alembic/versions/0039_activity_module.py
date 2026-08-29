"""Активность: измерение «модуль» (finances | pulse | planerka | …).

Пульс и Финансы живут в одном сервисе pnl — без module они неразличимы в
user_activity_days, а ретроспективно разделить нельзя. Строки до этой
миграции — 'finances' (их единицы, погрешность нулевая). Будущая страница
«Активность» в админке sa собирает по модулям со всех сервисов платформы.

Revision ID: 0039
Revises: 0038
"""
from alembic import op


revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.user_activity_days
            ADD COLUMN module VARCHAR(16) NOT NULL DEFAULT 'finances'
    """)
    op.execute("""
        ALTER TABLE pnl_service.user_activity_days
            DROP CONSTRAINT user_activity_days_pkey
    """)
    op.execute("""
        ALTER TABLE pnl_service.user_activity_days
            ADD PRIMARY KEY (user_id, day, module)
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM pnl_service.user_activity_days a
        WHERE a.module <> (
            SELECT min(b.module) FROM pnl_service.user_activity_days b
            WHERE b.user_id = a.user_id AND b.day = a.day
        )
    """)
    op.execute("""
        ALTER TABLE pnl_service.user_activity_days
            DROP CONSTRAINT user_activity_days_pkey
    """)
    op.execute("""
        ALTER TABLE pnl_service.user_activity_days DROP COLUMN module
    """)
    op.execute("""
        ALTER TABLE pnl_service.user_activity_days
            ADD PRIMARY KEY (user_id, day)
    """)
