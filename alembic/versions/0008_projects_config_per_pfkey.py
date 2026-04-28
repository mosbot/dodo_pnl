"""S8.5: projects_config — переезд owner_id → planfact_key_id

display_name / sort_order / dodo_unit_uuid / is_active — это атрибуты
аккаунта PlanFact, а не юзера. У всех пользователей с одним ключом
конфигурация проектов одна и та же, нет смысла дублировать. Дедуп при
миграции оставляет самую свежую запись (по updated_at).

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    # 1. Колонка planfact_key_id (nullable пока)
    op.add_column(
        "projects_config",
        sa.Column("planfact_key_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    # 2. Заполняем из users.planfact_key_id
    op.execute(f"""
        UPDATE {SCHEMA}.projects_config c
        SET planfact_key_id = u.planfact_key_id
        FROM {SCHEMA}.users u
        WHERE u.id = c.owner_id
    """)
    # 3. Удаляем строки для юзеров без ключа
    op.execute(
        f"DELETE FROM {SCHEMA}.projects_config WHERE planfact_key_id IS NULL"
    )
    # 4. Дедуп по (planfact_key_id, project_id) — оставляем самую свежую
    op.execute(f"""
        DELETE FROM {SCHEMA}.projects_config c
        USING (
            SELECT ctid FROM (
                SELECT ctid, ROW_NUMBER() OVER (
                    PARTITION BY planfact_key_id, project_id
                    ORDER BY updated_at DESC NULLS LAST
                ) AS rn
                FROM {SCHEMA}.projects_config
            ) ranked
            WHERE rn > 1
        ) dup
        WHERE c.ctid = dup.ctid
    """)
    # 5. NOT NULL и FK
    op.alter_column(
        "projects_config", "planfact_key_id",
        nullable=False, schema=SCHEMA,
    )
    # 6. Дроп старого PK (owner_id, project_id) и FK на users
    op.drop_constraint(
        "projects_config_pkey", "projects_config",
        schema=SCHEMA, type_="primary",
    )
    op.drop_constraint(
        "projects_config_owner_id_fkey", "projects_config",
        schema=SCHEMA, type_="foreignkey",
    )
    op.drop_column("projects_config", "owner_id", schema=SCHEMA)
    # 7. Новый PK + FK
    op.create_primary_key(
        "projects_config_pkey", "projects_config",
        ["planfact_key_id", "project_id"], schema=SCHEMA,
    )
    op.create_foreign_key(
        "projects_config_planfact_key_id_fkey",
        "projects_config", "planfact_keys",
        ["planfact_key_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="CASCADE",
    )


def downgrade() -> None:
    op.add_column(
        "projects_config",
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    # Восстановление owner_id неоднозначно — берём первого юзера с этим ключом.
    op.execute(f"""
        UPDATE {SCHEMA}.projects_config c
        SET owner_id = (
            SELECT u.id FROM {SCHEMA}.users u
            WHERE u.planfact_key_id = c.planfact_key_id
            ORDER BY u.id LIMIT 1
        )
    """)
    op.execute(f"DELETE FROM {SCHEMA}.projects_config WHERE owner_id IS NULL")
    op.alter_column("projects_config", "owner_id", nullable=False, schema=SCHEMA)
    op.drop_constraint(
        "projects_config_planfact_key_id_fkey", "projects_config",
        schema=SCHEMA, type_="foreignkey",
    )
    op.drop_constraint(
        "projects_config_pkey", "projects_config",
        schema=SCHEMA, type_="primary",
    )
    op.drop_column("projects_config", "planfact_key_id", schema=SCHEMA)
    op.create_primary_key(
        "projects_config_pkey", "projects_config",
        ["owner_id", "project_id"], schema=SCHEMA,
    )
    op.create_foreign_key(
        "projects_config_owner_id_fkey", "projects_config", "users",
        ["owner_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="CASCADE",
    )
