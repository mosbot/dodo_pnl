"""S8.6: разделяем доступ к проектам и их структуру

После S8.5 projects_config — общий на planfact_key (is_active, display_name,
sort_order, dodo_unit_uuid). Но «доступ юзера к проекту» логически
индивидуальный: один и тот же набор проектов под одним ключом — а юзеры
могут видеть разные подмножества.

Создаём `user_project_visibility(owner_id, project_id, is_visible)`.
По умолчанию всем юзерам видны все проекты — записи создаются только
когда админ выключает проект конкретному юзеру.

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    op.create_table(
        "user_project_visibility",
        sa.Column(
            "owner_id", sa.BigInteger(),
            sa.ForeignKey(
                f"{SCHEMA}.users.id",
                ondelete="CASCADE",
                name="user_project_visibility_owner_id_fkey",
            ),
            primary_key=True,
        ),
        sa.Column("project_id", sa.String(64), primary_key=True),
        sa.Column(
            "is_visible", sa.Boolean(),
            nullable=False, server_default=sa.text("true"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_user_visibility_owner", "user_project_visibility",
        ["owner_id"], schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_visibility_owner",
        table_name="user_project_visibility", schema=SCHEMA,
    )
    op.drop_table("user_project_visibility", schema=SCHEMA)
