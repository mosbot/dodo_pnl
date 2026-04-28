"""S8.3a: line_no в pnl_template + таблица pnl_metrics + targets per planfact_key

Cхема под формулы:
- pnl_template: добавить line_no INTEGER NOT NULL UNIQUE(planfact_key_id, line_no).
  Заполнить через ROW_NUMBER() PARTITION BY planfact_key_id ORDER BY sort_order.
- pnl_metrics: новая таблица KPI с формулами (code, label, formula, is_target,
  format, sort_order), привязка к planfact_key.
- targets / default_targets: переезжают с owner_id на planfact_key_id (метрики
  теперь per-key, таргеты тоже должны быть per-key).

Сидинг формул для существующих ключей — отдельным скриптом app.seed_metrics
(после применения миграции). Так миграция остаётся чистой DDL.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    # ---------- pnl_template: line_no ----------
    op.add_column(
        "pnl_template",
        sa.Column("line_no", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(f"""
        UPDATE {SCHEMA}.pnl_template t
        SET line_no = ranked.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY planfact_key_id
                ORDER BY sort_order, id
            ) AS rn
            FROM {SCHEMA}.pnl_template
        ) ranked
        WHERE t.id = ranked.id
    """)
    op.alter_column(
        "pnl_template", "line_no",
        nullable=False, schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_pnl_template_pfkey_lineno", "pnl_template",
        ["planfact_key_id", "line_no"], schema=SCHEMA,
    )

    # ---------- pnl_metrics ----------
    op.create_table(
        "pnl_metrics",
        sa.Column(
            "planfact_key_id", sa.BigInteger(),
            sa.ForeignKey(
                f"{SCHEMA}.planfact_keys.id",
                ondelete="CASCADE",
                name="pnl_metrics_planfact_key_id_fkey",
            ),
            primary_key=True,
        ),
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column(
            "is_target", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "format", sa.String(16),
            nullable=False, server_default=sa.text("'pct'"),
            comment="pct | rub | x",
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pnl_metrics_pfkey_sort", "pnl_metrics",
        ["planfact_key_id", "sort_order"], schema=SCHEMA,
    )

    # ---------- targets: owner_id → planfact_key_id ----------
    # У targets PK был на id (autoincrement) + UniqueConstraint на (owner_id,
    # project_id, metric_code). После миграции — composite PK (planfact_key_id,
    # project_id, metric_code), id и UniqueConstraint выкидываем.
    _add_pfkey_column("targets")
    _dedup_by("targets", ["planfact_key_id", "project_id", "metric_code"])
    op.alter_column("targets", "planfact_key_id", nullable=False, schema=SCHEMA)
    op.drop_constraint(
        "uq_targets_owner_project_metric", "targets",
        schema=SCHEMA, type_="unique",
    )
    op.drop_index("ix_targets_owner_project", table_name="targets", schema=SCHEMA)
    op.drop_constraint("targets_pkey", "targets", schema=SCHEMA, type_="primary")
    op.drop_constraint(
        "targets_owner_id_fkey", "targets",
        schema=SCHEMA, type_="foreignkey",
    )
    op.drop_column("targets", "id", schema=SCHEMA)
    op.drop_column("targets", "owner_id", schema=SCHEMA)
    op.create_primary_key(
        "targets_pkey", "targets",
        ["planfact_key_id", "project_id", "metric_code"], schema=SCHEMA,
    )
    op.create_foreign_key(
        "targets_planfact_key_id_fkey", "targets", "planfact_keys",
        ["planfact_key_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="CASCADE",
    )
    op.create_index(
        "ix_targets_pfkey_project", "targets",
        ["planfact_key_id", "project_id"], schema=SCHEMA,
    )

    # ---------- default_targets: owner_id → planfact_key_id ----------
    # Composite PK (owner_id, metric_code).
    _add_pfkey_column("default_targets")
    _dedup_by("default_targets", ["planfact_key_id", "metric_code"])
    op.alter_column(
        "default_targets", "planfact_key_id",
        nullable=False, schema=SCHEMA,
    )
    op.drop_constraint(
        "default_targets_pkey", "default_targets",
        schema=SCHEMA, type_="primary",
    )
    op.drop_constraint(
        "default_targets_owner_id_fkey", "default_targets",
        schema=SCHEMA, type_="foreignkey",
    )
    op.drop_column("default_targets", "owner_id", schema=SCHEMA)
    op.create_primary_key(
        "default_targets_pkey", "default_targets",
        ["planfact_key_id", "metric_code"], schema=SCHEMA,
    )
    op.create_foreign_key(
        "default_targets_planfact_key_id_fkey",
        "default_targets", "planfact_keys",
        ["planfact_key_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="CASCADE",
    )


def _add_pfkey_column(table: str) -> None:
    """Добавить planfact_key_id в таблицу с owner_id и заполнить из users."""
    op.add_column(
        table,
        sa.Column("planfact_key_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(f"""
        UPDATE {SCHEMA}.{table} t
        SET planfact_key_id = u.planfact_key_id
        FROM {SCHEMA}.users u
        WHERE u.id = t.owner_id
    """)
    op.execute(f"DELETE FROM {SCHEMA}.{table} WHERE planfact_key_id IS NULL")


def _dedup_by(table: str, partition_cols: list[str]) -> None:
    """Оставить только самую свежую запись внутри каждой группы partition_cols
    (по updated_at DESC, NULL последними)."""
    partition = ", ".join(partition_cols)
    op.execute(f"""
        DELETE FROM {SCHEMA}.{table} t
        USING (
            SELECT ctid FROM (
                SELECT ctid, ROW_NUMBER() OVER (
                    PARTITION BY {partition}
                    ORDER BY updated_at DESC NULLS LAST
                ) AS rn
                FROM {SCHEMA}.{table}
            ) ranked
            WHERE rn > 1
        ) dup
        WHERE t.ctid = dup.ctid
    """)


def downgrade() -> None:
    # ---------- default_targets: planfact_key_id → owner_id ----------
    op.add_column(
        "default_targets",
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(f"""
        UPDATE {SCHEMA}.default_targets t
        SET owner_id = (
            SELECT u.id FROM {SCHEMA}.users u
            WHERE u.planfact_key_id = t.planfact_key_id
            ORDER BY u.id LIMIT 1
        )
    """)
    op.execute(f"DELETE FROM {SCHEMA}.default_targets WHERE owner_id IS NULL")
    op.alter_column("default_targets", "owner_id", nullable=False, schema=SCHEMA)
    op.drop_constraint(
        "default_targets_planfact_key_id_fkey", "default_targets",
        schema=SCHEMA, type_="foreignkey",
    )
    op.drop_constraint(
        "default_targets_pkey", "default_targets",
        schema=SCHEMA, type_="primary",
    )
    op.drop_column("default_targets", "planfact_key_id", schema=SCHEMA)
    op.create_primary_key(
        "default_targets_pkey", "default_targets",
        ["owner_id", "metric_code"], schema=SCHEMA,
    )
    op.create_foreign_key(
        "default_targets_owner_id_fkey", "default_targets", "users",
        ["owner_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="CASCADE",
    )

    # ---------- targets: planfact_key_id → owner_id (вернуть id + UC) ----------
    op.add_column(
        "targets",
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "targets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=True),
        schema=SCHEMA,
    )
    op.execute(f"""
        UPDATE {SCHEMA}.targets t
        SET owner_id = (
            SELECT u.id FROM {SCHEMA}.users u
            WHERE u.planfact_key_id = t.planfact_key_id
            ORDER BY u.id LIMIT 1
        )
    """)
    op.execute(f"DELETE FROM {SCHEMA}.targets WHERE owner_id IS NULL")
    op.alter_column("targets", "owner_id", nullable=False, schema=SCHEMA)
    op.execute(f"""
        CREATE SEQUENCE IF NOT EXISTS {SCHEMA}.targets_id_seq;
        UPDATE {SCHEMA}.targets SET id = nextval('{SCHEMA}.targets_id_seq');
        ALTER TABLE {SCHEMA}.targets ALTER COLUMN id SET DEFAULT nextval('{SCHEMA}.targets_id_seq');
        ALTER SEQUENCE {SCHEMA}.targets_id_seq OWNED BY {SCHEMA}.targets.id;
    """)
    op.alter_column("targets", "id", nullable=False, schema=SCHEMA)
    op.drop_index("ix_targets_pfkey_project", table_name="targets", schema=SCHEMA)
    op.drop_constraint(
        "targets_planfact_key_id_fkey", "targets",
        schema=SCHEMA, type_="foreignkey",
    )
    op.drop_constraint("targets_pkey", "targets", schema=SCHEMA, type_="primary")
    op.drop_column("targets", "planfact_key_id", schema=SCHEMA)
    op.create_primary_key("targets_pkey", "targets", ["id"], schema=SCHEMA)
    op.create_foreign_key(
        "targets_owner_id_fkey", "targets", "users",
        ["owner_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_targets_owner_project_metric", "targets",
        ["owner_id", "project_id", "metric_code"], schema=SCHEMA,
    )
    op.create_index(
        "ix_targets_owner_project", "targets",
        ["owner_id", "project_id"], schema=SCHEMA,
    )

    # ---------- pnl_metrics ----------
    op.drop_index("ix_pnl_metrics_pfkey_sort", table_name="pnl_metrics", schema=SCHEMA)
    op.drop_table("pnl_metrics", schema=SCHEMA)

    # ---------- pnl_template: line_no ----------
    op.drop_constraint(
        "uq_pnl_template_pfkey_lineno", "pnl_template",
        schema=SCHEMA, type_="unique",
    )
    op.drop_column("pnl_template", "line_no", schema=SCHEMA)
