"""S11.6: ops_metrics / ops_targets / ops_project_targets → planfact_key_id

Раньше ops-таблицы хранились per-user (owner_id), что для команд из
нескольких юзеров на одном PF-ключе означало:
  - дублирующие синки в Dodo IS API под одним токеном,
  - 5 копий тех же чисел в БД,
  - каждый видит свой freshness-бейдж.

Переносим на planfact_key_id (как уже сделано с projects_config в S8.5
и cache_history в S3.5). Данные в Dodo IS зависят от unit'а, а не от
юзера, поэтому общий уровень корректен.

Backfill-стратегия:
  1. ALTER TABLE add planfact_key_id BIGINT NULL.
  2. UPDATE planfact_key_id = users.planfact_key_id для существующих строк.
  3. DELETE строки где planfact_key_id оказался NULL (юзер без ключа —
     сиротские данные, очищаем).
  4. Дедуп: при коллизии (несколько юзеров одного ключа имели одинаковую
     запись) оставляем САМУЮ СВЕЖУЮ по updated_at — это последний синк/
     апдейт, остальные стирает CTE.
  5. SET NOT NULL + FK + новый PK + DROP owner_id.

Revision ID: 0013
Revises: 0012
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "pnl_service"


def _migrate_table(table: str, pk_cols: list[str]) -> None:
    """Шаги (1)-(5) для одной из ops-таблиц.

    pk_cols — колонки нового PK после migration (без planfact_key_id —
    он добавляется автоматически в начало).
    """
    # 1. Добавляем nullable колонку.
    op.add_column(
        table,
        sa.Column("planfact_key_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    # 2. Backfill из users.planfact_key_id.
    op.execute(
        f"""
        UPDATE {SCHEMA}.{table} t
        SET planfact_key_id = u.planfact_key_id
        FROM {SCHEMA}.users u
        WHERE u.id = t.owner_id
        """
    )
    # 3. Удаляем сироты (юзер без ключа — данные не привязать).
    op.execute(
        f"DELETE FROM {SCHEMA}.{table} WHERE planfact_key_id IS NULL"
    )
    # 4. Дедуп по новому PK: оставляем самое свежее по updated_at.
    cols_join = ", ".join(pk_cols)
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.{table} t1
        WHERE EXISTS (
            SELECT 1 FROM {SCHEMA}.{table} t2
            WHERE t2.planfact_key_id = t1.planfact_key_id
              AND {' AND '.join(f't2.{c} = t1.{c}' for c in pk_cols)}
              AND (
                t2.updated_at > t1.updated_at
                OR (t2.updated_at = t1.updated_at AND t2.owner_id < t1.owner_id)
              )
        )
        """
    )
    # 5. NOT NULL + FK + новый PK + DROP owner_id.
    op.alter_column(
        table, "planfact_key_id", nullable=False, schema=SCHEMA,
    )
    op.create_foreign_key(
        f"{table}_planfact_key_id_fkey",
        table, "planfact_keys",
        ["planfact_key_id"], ["id"],
        ondelete="CASCADE",
        source_schema=SCHEMA, referent_schema=SCHEMA,
    )
    op.drop_constraint(f"{table}_pkey", table, type_="primary", schema=SCHEMA)
    # owner_id больше не нужен — удаляем (FK к users отвалится сам, т.к.
    # cascade-fk был на owner_id, а мы его дропаем).
    op.drop_column(table, "owner_id", schema=SCHEMA)
    op.create_primary_key(
        f"{table}_pkey", table,
        ["planfact_key_id", *pk_cols],
        schema=SCHEMA,
    )


def upgrade() -> None:
    _migrate_table("ops_metrics",          ["project_id", "period_month"])
    _migrate_table("ops_targets",          ["metric_code"])
    _migrate_table("ops_project_targets",  ["project_id", "metric_code"])

    # Доп. индекс для быстрого поиска "когда последний раз ключ синкался"
    # (max updated_at) — используется в ops_freshness.
    op.create_index(
        "ix_ops_metrics_pfkey_period",
        "ops_metrics",
        ["planfact_key_id", "period_month"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Полный откат не реализуем — owner_id потерян, восстановить нельзя.
    Сценарий downgrade нужен только для тестов; в проде S11.6 не отзываем.
    """
    raise NotImplementedError(
        "Откат S11.6 невозможен — owner_id потерян, реконструкция бессмысленна."
    )
