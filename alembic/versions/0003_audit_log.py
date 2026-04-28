"""S5.2: audit_log

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB


revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("details", JSONB(), nullable=True),
        sa.Column("ip", INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{SCHEMA}.users.id"], ondelete="SET NULL"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"], schema=SCHEMA)
    op.create_index("ix_audit_log_action", "audit_log", ["action"], schema=SCHEMA)
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log", schema=SCHEMA)
    op.drop_index("ix_audit_log_action", table_name="audit_log", schema=SCHEMA)
    op.drop_index("ix_audit_log_user_id", table_name="audit_log", schema=SCHEMA)
    op.drop_table("audit_log", schema=SCHEMA)
