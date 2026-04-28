"""S8.1: pnl_template и category_mapping — привязка к planfact_keys

Шаблон P&L и mapping категорий зависят от структуры аккаунта PlanFact
(один ключ = одна структура), а не от пользователя. Несколько юзеров с
одним ключом теперь делят один шаблон. Write-доступ — admin only (это
проставит роутер; БД не различает админов).

Миграция:
1. pnl_template: drop FK на users.id, drop owner_id, add planfact_key_id
   (FK на planfact_keys.id ON DELETE CASCADE), заполнить из users.planfact_key_id.
   Записи для юзеров без ключа удаляются (никто их не увидит — некому).
2. category_mapping (таблица пустая в проде): то же самое + перепишем PK.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def upgrade() -> None:
    # ---------- pnl_template ----------

    # 1. Колонка planfact_key_id (nullable пока)
    op.add_column(
        "pnl_template",
        sa.Column("planfact_key_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )

    # 2. Заполняем из users.planfact_key_id
    op.execute(f"""
        UPDATE {SCHEMA}.pnl_template t
        SET planfact_key_id = u.planfact_key_id
        FROM {SCHEMA}.users u
        WHERE u.id = t.owner_id
    """)

    # 3. Удаляем то, что не смогли привязать (юзеры без ключа). Эти шаблоны
    #    никому не покажешь — после миграции read идёт по planfact_key_id.
    op.execute(
        f"DELETE FROM {SCHEMA}.pnl_template WHERE planfact_key_id IS NULL"
    )

    # 4. Если у двух юзеров с одним ключом был свой шаблон — после переноса
    #    получается дубль. Оставляем самый свежий (по updated_at), остальные —
    #    в утиль. Дедуп идёт по (planfact_key_id, path_lc).
    op.execute(f"""
        DELETE FROM {SCHEMA}.pnl_template t
        USING (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY planfact_key_id, path_lc
                    ORDER BY updated_at DESC, id DESC
                ) AS rn
                FROM {SCHEMA}.pnl_template
            ) ranked
            WHERE rn > 1
        ) dup
        WHERE t.id = dup.id
    """)

    # 5. Сделать NOT NULL
    op.alter_column(
        "pnl_template", "planfact_key_id",
        nullable=False, schema=SCHEMA,
    )

    # 6. FK + индексы
    op.create_foreign_key(
        "pnl_template_planfact_key_id_fkey",
        "pnl_template", "planfact_keys",
        ["planfact_key_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA,
        ondelete="CASCADE",
    )

    # 7. Старые индексы по owner_id и FK на users — выкидываем
    op.drop_index("ix_template_owner_path", table_name="pnl_template", schema=SCHEMA)
    op.drop_index("ix_template_owner_parent", table_name="pnl_template", schema=SCHEMA)
    op.drop_index("ix_template_owner_sort", table_name="pnl_template", schema=SCHEMA)
    op.drop_constraint(
        "pnl_template_owner_id_fkey", "pnl_template",
        schema=SCHEMA, type_="foreignkey",
    )
    op.drop_column("pnl_template", "owner_id", schema=SCHEMA)

    # 8. Новые индексы по planfact_key_id
    op.create_index(
        "ix_template_pfkey_path", "pnl_template",
        ["planfact_key_id", "path_lc"], schema=SCHEMA,
    )
    op.create_index(
        "ix_template_pfkey_parent", "pnl_template",
        ["planfact_key_id", "parent_id"], schema=SCHEMA,
    )
    op.create_index(
        "ix_template_pfkey_sort", "pnl_template",
        ["planfact_key_id", "sort_order"], schema=SCHEMA,
    )

    # ---------- category_mapping ----------

    # Таблица пустая в проде, но миграция всё равно аккуратная: дропаем PK,
    # потом колонку, добавляем новую и переустанавливаем PK.
    op.drop_constraint(
        "category_mapping_pkey", "category_mapping",
        schema=SCHEMA, type_="primary",
    )
    op.drop_index(
        "ix_mapping_owner_code", table_name="category_mapping", schema=SCHEMA,
    )
    op.drop_constraint(
        "category_mapping_owner_id_fkey", "category_mapping",
        schema=SCHEMA, type_="foreignkey",
    )
    op.drop_column("category_mapping", "owner_id", schema=SCHEMA)

    op.add_column(
        "category_mapping",
        sa.Column("planfact_key_id", sa.BigInteger(), nullable=False),
        schema=SCHEMA,
    )
    op.create_primary_key(
        "category_mapping_pkey", "category_mapping",
        ["planfact_key_id", "planfact_category_id"], schema=SCHEMA,
    )
    op.create_foreign_key(
        "category_mapping_planfact_key_id_fkey",
        "category_mapping", "planfact_keys",
        ["planfact_key_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_mapping_pfkey_code", "category_mapping",
        ["planfact_key_id", "pnl_code"], schema=SCHEMA,
    )


def downgrade() -> None:
    # ---------- category_mapping rollback ----------
    op.drop_index("ix_mapping_pfkey_code", table_name="category_mapping", schema=SCHEMA)
    op.drop_constraint(
        "category_mapping_planfact_key_id_fkey", "category_mapping",
        schema=SCHEMA, type_="foreignkey",
    )
    op.drop_constraint(
        "category_mapping_pkey", "category_mapping",
        schema=SCHEMA, type_="primary",
    )
    op.drop_column("category_mapping", "planfact_key_id", schema=SCHEMA)
    op.add_column(
        "category_mapping",
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "category_mapping_owner_id_fkey", "category_mapping", "users",
        ["owner_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="CASCADE",
    )
    op.create_primary_key(
        "category_mapping_pkey", "category_mapping",
        ["owner_id", "planfact_category_id"], schema=SCHEMA,
    )
    op.create_index(
        "ix_mapping_owner_code", "category_mapping",
        ["owner_id", "pnl_code"], schema=SCHEMA,
    )

    # ---------- pnl_template rollback ----------
    op.drop_index("ix_template_pfkey_sort", table_name="pnl_template", schema=SCHEMA)
    op.drop_index("ix_template_pfkey_parent", table_name="pnl_template", schema=SCHEMA)
    op.drop_index("ix_template_pfkey_path", table_name="pnl_template", schema=SCHEMA)
    op.add_column(
        "pnl_template",
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    # Не пытаемся восстановить owner_id точно (mapping ключ→юзер
    # многозначен). Заполним id первого юзера с этим ключом.
    op.execute(f"""
        UPDATE {SCHEMA}.pnl_template t
        SET owner_id = (
            SELECT u.id FROM {SCHEMA}.users u
            WHERE u.planfact_key_id = t.planfact_key_id
            ORDER BY u.id LIMIT 1
        )
    """)
    op.execute(f"DELETE FROM {SCHEMA}.pnl_template WHERE owner_id IS NULL")
    op.alter_column("pnl_template", "owner_id", nullable=False, schema=SCHEMA)
    op.create_foreign_key(
        "pnl_template_owner_id_fkey", "pnl_template", "users",
        ["owner_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="CASCADE",
    )
    op.drop_constraint(
        "pnl_template_planfact_key_id_fkey", "pnl_template",
        schema=SCHEMA, type_="foreignkey",
    )
    op.drop_column("pnl_template", "planfact_key_id", schema=SCHEMA)
    op.create_index(
        "ix_template_owner_path", "pnl_template",
        ["owner_id", "path_lc"], schema=SCHEMA,
    )
    op.create_index(
        "ix_template_owner_parent", "pnl_template",
        ["owner_id", "parent_id"], schema=SCHEMA,
    )
    op.create_index(
        "ix_template_owner_sort", "pnl_template",
        ["owner_id", "sort_order"], schema=SCHEMA,
    )
