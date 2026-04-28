"""S4.6: planfact_keys catalog + users.planfact_key_id

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    # 1. Новая таблица каталога
    op.create_table(
        "planfact_keys",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("NOW()"),
        ),
        schema=SCHEMA,
    )

    # 2. Колонка-ссылка на каталог
    op.add_column(
        "users",
        sa.Column("planfact_key_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "users_planfact_key_id_fkey",
        "users", "planfact_keys",
        ["planfact_key_id"], ["id"],
        ondelete="SET NULL",
        source_schema=SCHEMA, referent_schema=SCHEMA,
    )

    # 3. Перенос данных: для каждого юзера с непустым planfact_api_key
    #    создаём запись в planfact_keys (auto-name = "<username>'s key"),
    #    проставляем users.planfact_key_id. Если у двух юзеров одинаковый
    #    ключ — реюзаем существующую запись.
    op.execute(
        f"""
        WITH inserted AS (
            INSERT INTO {SCHEMA}.planfact_keys (name, api_key, note)
            SELECT
                (u.username || '''s key') AS name,
                u.planfact_api_key,
                'Автоматически перенесён из users.planfact_api_key'
            FROM {SCHEMA}.users u
            WHERE u.planfact_api_key IS NOT NULL AND u.planfact_api_key <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM {SCHEMA}.planfact_keys pk
                  WHERE pk.api_key = u.planfact_api_key
              )
            RETURNING id, api_key
        )
        UPDATE {SCHEMA}.users u
        SET planfact_key_id = pk.id
        FROM {SCHEMA}.planfact_keys pk
        WHERE u.planfact_api_key IS NOT NULL
          AND u.planfact_api_key <> ''
          AND pk.api_key = u.planfact_api_key
        """
    )

    # 4. Старая колонка больше не нужна
    op.drop_column("users", "planfact_api_key", schema=SCHEMA)


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("planfact_api_key", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    # Перенести обратно: api_key из planfact_keys в users
    op.execute(
        f"""
        UPDATE {SCHEMA}.users u
        SET planfact_api_key = pk.api_key
        FROM {SCHEMA}.planfact_keys pk
        WHERE u.planfact_key_id = pk.id
        """
    )
    op.drop_constraint("users_planfact_key_id_fkey", "users", schema=SCHEMA, type_="foreignkey")
    op.drop_column("users", "planfact_key_id", schema=SCHEMA)
    op.drop_table("planfact_keys", schema=SCHEMA)
