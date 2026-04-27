"""S1.2: users + sessions

Создаём фундамент multi-tenant: таблицы пользователей и серверных сессий.
Все таблицы в схеме pnl_service.

Revision ID: 0001
Revises:
Create Date: 2026-04-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET


revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("dodois_credentials_name", sa.String(128), nullable=True),
        sa.Column("planfact_api_key", sa.Text(), nullable=True),
        sa.Column(
            "is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "sessions",
        sa.Column("token", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", INET(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.users.id"],
            name="sessions_user_id_fkey",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )

    op.create_index(
        "ix_sessions_user_id", "sessions", ["user_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_sessions_expires_at", "sessions", ["expires_at"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_expires_at", table_name="sessions", schema=SCHEMA)
    op.drop_index("ix_sessions_user_id", table_name="sessions", schema=SCHEMA)
    op.drop_table("sessions", schema=SCHEMA)
    op.drop_table("users", schema=SCHEMA)
