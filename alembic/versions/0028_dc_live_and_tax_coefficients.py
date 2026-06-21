"""DC расчётный (courier labor) + налоговые коэффициенты KC/DC (per-tenant).

- ops_metrics.dc_live_pct — расчётный Delivery Cost%: net wage курьерских смен
  (staffType == 'Courier' из /staff/incentives-by-members) / выручка × 100.
  Зеркало kc_live_pct (где курьеры наоборот исключены).
- planfact_keys.kc_tax_coefficient / dc_tax_coefficient — множитель «налоги»,
  применяется к расчётным KC%/DC% на чтении (raw в БД, ×коэф. на отдаче).
  Default 1.0 → поведение KC не меняется, пока коэффициент не задан.
- planfact_keys.dc_live_enabled — флаг показа расчётного DC (default FALSE,
  деплой безопасен; включается на тенанте, когда нужно).

Revision ID: 0028
Revises: 0027
"""
from alembic import op


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        ADD COLUMN dc_live_pct DOUBLE PRECISION
    """)
    op.execute("""
        ALTER TABLE pnl_service.planfact_keys
        ADD COLUMN kc_tax_coefficient DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        ADD COLUMN dc_tax_coefficient DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        ADD COLUMN dc_live_enabled    BOOLEAN          NOT NULL DEFAULT FALSE
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE pnl_service.ops_metrics DROP COLUMN dc_live_pct")
    op.execute(
        "ALTER TABLE pnl_service.planfact_keys "
        "DROP COLUMN kc_tax_coefficient, "
        "DROP COLUMN dc_tax_coefficient, "
        "DROP COLUMN dc_live_enabled"
    )
