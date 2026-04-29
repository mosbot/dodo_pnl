"""S3.4: таблица cache_history + planfact_keys.live_months_window

Кэшируем агрегаты P&L (cat_totals, revenue_by_channel, active_project_ids
и т.п.) для закрытых месяцев — те, что не попадают в «live-окно» ключа.
Live-окно = текущий месяц + N-1 предыдущих, где N = live_months_window
(по умолчанию 2). Старые месяцы читаем из cache_history, при отсутствии —
собираем из PF и замораживаем.

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    # Глубина live-окна на ключ. По умолчанию 2 — текущий + 1 предыдущий.
    # Если бухгалтер закрывает квартал — можно поставить 4 (текущий + 3
    # предыдущих).
    op.add_column(
        "planfact_keys",
        sa.Column(
            "live_months_window", sa.Integer(),
            nullable=False, server_default=sa.text("2"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "cache_history",
        sa.Column(
            "planfact_key_id", sa.BigInteger(),
            sa.ForeignKey(
                f"{SCHEMA}.planfact_keys.id",
                ondelete="CASCADE",
                name="cache_history_planfact_key_id_fkey",
            ),
            primary_key=True,
        ),
        sa.Column(
            "kind", sa.String(32),
            primary_key=True,
            comment="planfact_pnl (зарезервировано под расширение)",
        ),
        sa.Column("period_month", sa.String(7), primary_key=True,
                  comment="YYYY-MM"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "frozen_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "frozen_by_user_id", sa.BigInteger(), nullable=True,
            comment="кто инициировал заморозку (если known)",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_cache_history_pfkey_period",
        "cache_history",
        ["planfact_key_id", "period_month"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cache_history_pfkey_period",
        table_name="cache_history", schema=SCHEMA,
    )
    op.drop_table("cache_history", schema=SCHEMA)
    op.drop_column("planfact_keys", "live_months_window", schema=SCHEMA)
