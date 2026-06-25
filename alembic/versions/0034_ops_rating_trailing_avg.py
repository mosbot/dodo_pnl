"""Скользящее среднее рейтингов в ops_metrics: РКО за 12 недель, РС за 6 проверок.

Период-независимые «последние доступные» средние из Controlling history:
  - rko_avg12w — среднее rate последних 12 недельных периодов РКО;
  - rs_avg6    — среднее rate последних 6 проверок РС.
Считаются ТОЛЬКО при синке текущего месяца (`_run_ops_sync`), хранятся на
строке текущего месяца; в ответ /api/pnl подтягиваются «последние непустые»
независимо от выбранного периода (карточка всегда показывает свежее среднее).

Revision ID: 0034
Revises: 0033
"""
from alembic import op


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        ADD COLUMN rko_avg12w INTEGER,
        ADD COLUMN rs_avg6    INTEGER
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE pnl_service.ops_metrics "
        "DROP COLUMN rko_avg12w, "
        "DROP COLUMN rs_avg6"
    )
