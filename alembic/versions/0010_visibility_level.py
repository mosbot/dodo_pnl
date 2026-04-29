"""S8.8: уровни видимости метрик

Числовая шкала: каждый юзер имеет visibility_level (0..100), у каждой
метрики min_visibility_level. Юзер видит метрику если его уровень
>= уровню метрики. Метрики уже per-planfact_key (S8.3), так что
ограничение фактически работает в разрезе ключа.

Условные дефолты, которые проставит сидинг:
  10  — Управляющий пиццерией: REVENUE, UC, LC, DC, TC
  30  — Территориальный: + RENT, MARKETING, FRANCHISE, OTHER_OPEX, MARGIN
  60  — Директор: + MGMT, OPERATING_PROFIT, OTHER_INCOME, EBITDA
  100 — Партнёр: + INTEREST, TAX, NET_PROFIT, DIVIDENDS

users.visibility_level — по умолчанию 100 (партнёр, видит всё), чтобы
существующие юзеры ничего не потеряли. Админ потом проставит нужный
уровень.

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "visibility_level", sa.Integer(),
            nullable=False, server_default=sa.text("100"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "pnl_metrics",
        sa.Column(
            "min_visibility_level", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("pnl_metrics", "min_visibility_level", schema=SCHEMA)
    op.drop_column("users", "visibility_level", schema=SCHEMA)
