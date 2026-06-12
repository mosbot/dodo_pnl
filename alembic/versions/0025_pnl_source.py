"""S20: planfact_keys.pnl_source — источник P&L-агрегата.

'raw' (default) — legacy GET /operations; 'shadow' — raw + фоновая сверка
v2; 'v2' — POST /api/v2/reports/opu с fallback на raw.
См. docs/audits/v2-reports-migration-plan.md.

Деплой безопасен: default 'raw' — поведение не меняется, пока флаг не
переключён вручную (админка / SQL).

Revision ID: 0025
Revises: 0024
"""
from alembic import op


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.planfact_keys
        ADD COLUMN pnl_source VARCHAR(16) NOT NULL DEFAULT 'raw'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE pnl_service.planfact_keys DROP COLUMN pnl_source")
