"""S8.2: drop category_mapping (dead code)

Таблица никогда не использовалась в проде (0 строк), UI её не зовёт,
override живёт через PATCH /api/template/{id} (поле pnl_code узла
шаблона). Сносим целиком.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    op.drop_table("category_mapping", schema=SCHEMA)


def downgrade() -> None:
    # Воссоздаём таблицу в форме после миграции 0005 (per planfact_key).
    op.create_table(
        "category_mapping",
        sa.Column("planfact_key_id", sa.BigInteger(), nullable=False),
        sa.Column("planfact_category_id", sa.String(64), nullable=False),
        sa.Column("pnl_code", sa.String(32), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint(
            "planfact_key_id", "planfact_category_id",
            name="category_mapping_pkey",
        ),
        sa.ForeignKeyConstraint(
            ["planfact_key_id"],
            [f"{SCHEMA}.planfact_keys.id"],
            name="category_mapping_planfact_key_id_fkey",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_mapping_pfkey_code", "category_mapping",
        ["planfact_key_id", "pnl_code"], schema=SCHEMA,
    )
