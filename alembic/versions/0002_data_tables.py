"""S2.1: data tables (multi-tenant, owner_id everywhere)

Создаём все 9 таблиц данных с колонкой owner_id BIGINT REFERENCES users(id)
ON DELETE CASCADE. Composite PK / UNIQUE с owner_id, чтобы один project_id /
metric_code мог быть у разных пользователей.

Никаких данных не переносим (по согласованию — стартуем с чистого листа).

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"
USERS_FK = f"{SCHEMA}.users.id"


def upgrade() -> None:
    # ----- targets -----
    op.create_table(
        "targets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("metric_code", sa.String(32), nullable=False),
        sa.Column("target_pct", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["owner_id"], [USERS_FK], ondelete="CASCADE"),
        sa.UniqueConstraint("owner_id", "project_id", "metric_code",
                            name="uq_targets_owner_project_metric"),
        schema=SCHEMA,
    )
    op.create_index("ix_targets_owner_project", "targets",
                    ["owner_id", "project_id"], schema=SCHEMA)

    # ----- default_targets -----
    op.create_table(
        "default_targets",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("metric_code", sa.String(32), primary_key=True),
        sa.Column("target_pct", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["owner_id"], [USERS_FK], ondelete="CASCADE"),
        schema=SCHEMA,
    )

    # ----- category_mapping -----
    op.create_table(
        "category_mapping",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("planfact_category_id", sa.String(64), primary_key=True),
        sa.Column("pnl_code", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["owner_id"], [USERS_FK], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_mapping_owner_code", "category_mapping",
                    ["owner_id", "pnl_code"], schema=SCHEMA)

    # ----- app_settings -----
    op.create_table(
        "app_settings",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["owner_id"], [USERS_FK], ondelete="CASCADE"),
        schema=SCHEMA,
    )

    # ----- projects_config -----
    op.create_table(
        "projects_config",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.String(64), primary_key=True),
        sa.Column("is_active", sa.Boolean(),
                  nullable=False, server_default=sa.text("true")),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("dodo_unit_uuid", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["owner_id"], [USERS_FK], ondelete="CASCADE"),
        schema=SCHEMA,
    )

    # ----- ops_metrics -----
    op.create_table(
        "ops_metrics",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.String(64), primary_key=True),
        sa.Column("period_month", sa.String(7), primary_key=True),
        sa.Column("orders_per_courier_h", sa.Float(), nullable=True),
        sa.Column("products_per_h", sa.Float(), nullable=True),
        sa.Column("revenue_per_person_h", sa.Float(), nullable=True),
        sa.Column("late_delivery_certs", sa.Integer(), nullable=True),
        sa.Column("delivery_orders_count", sa.Integer(), nullable=True),
        sa.Column("late_delivery_certs_pct", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["owner_id"], [USERS_FK], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_ops_metrics_owner_period", "ops_metrics",
                    ["owner_id", "period_month"], schema=SCHEMA)

    # ----- ops_targets -----
    op.create_table(
        "ops_targets",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("metric_code", sa.String(32), primary_key=True),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["owner_id"], [USERS_FK], ondelete="CASCADE"),
        schema=SCHEMA,
    )

    # ----- ops_project_targets -----
    op.create_table(
        "ops_project_targets",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True),
        sa.Column("project_id", sa.String(64), primary_key=True),
        sa.Column("metric_code", sa.String(32), primary_key=True),
        sa.Column("target_value", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["owner_id"], [USERS_FK], ondelete="CASCADE"),
        schema=SCHEMA,
    )

    # ----- pnl_template -----
    op.create_table(
        "pnl_template",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        # parent_id — self-FK на pnl_template.id того же owner. Без FK на уровне
        # БД для упрощения batch-replace; целостность поддерживается приложением.
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("path_lc", sa.Text(), nullable=False),
        sa.Column("is_calc", sa.Boolean(),
                  nullable=False, server_default=sa.text("false")),
        sa.Column("is_leaf", sa.Boolean(),
                  nullable=False, server_default=sa.text("false")),
        sa.Column("pnl_code", sa.String(32), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["owner_id"], [USERS_FK], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_template_owner_path", "pnl_template",
                    ["owner_id", "path_lc"], schema=SCHEMA)
    op.create_index("ix_template_owner_parent", "pnl_template",
                    ["owner_id", "parent_id"], schema=SCHEMA)
    op.create_index("ix_template_owner_sort", "pnl_template",
                    ["owner_id", "sort_order"], schema=SCHEMA)


def downgrade() -> None:
    for tbl in (
        "pnl_template",
        "ops_project_targets",
        "ops_targets",
        "ops_metrics",
        "projects_config",
        "app_settings",
        "category_mapping",
        "default_targets",
        "targets",
    ):
        op.drop_table(tbl, schema=SCHEMA)
