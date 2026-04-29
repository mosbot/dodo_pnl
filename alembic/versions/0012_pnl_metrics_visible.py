"""S8.9: pnl_metrics.is_visible — какие метрики рисовать на карточке.

Флаг управляет ТОЛЬКО рендером плиток на главной (фин-блок + блок метрик).
Backend всё равно считает все метрики из формул — они нужны для drill'а
и для совместимости с возможным API. is_visible=False просто прячет плитку.

Default = True: чтобы существующие метрики продолжали отображаться без
ручной правки.

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    op.add_column(
        "pnl_metrics",
        sa.Column(
            "is_visible", sa.Boolean(),
            nullable=False, server_default=sa.text("true"),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("pnl_metrics", "is_visible", schema=SCHEMA)
