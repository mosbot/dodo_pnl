"""РКО (рейтинг клиентского опыта) и РС (рейтинг стандартов) в ops_metrics.

Источник — Controlling API Dodo IS:
  РКО — GET /controlling/ratings/customer-experience
  РС  — GET /controlling/ratings/standards
Значение per-unit `rate` 0..100 за ТЕКУЩИЙ период (не календарный месяц),
поэтому пишется только при синке текущего месяца. На UI — целое 0..100,
direction higher (больше — лучше).

Revision ID: 0032
Revises: 0031
"""
from alembic import op


revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        ADD COLUMN rko_rate INTEGER,
        ADD COLUMN rs_rate  INTEGER
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE pnl_service.ops_metrics "
        "DROP COLUMN rko_rate, DROP COLUMN rs_rate"
    )
