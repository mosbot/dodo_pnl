"""S14.1: per-month targets — добавить period_month в PK 4 таблиц целей.

Раньше цели жили без разреза по месяцу:
  - targets[pf_key, project_id, metric_code]  → одна цель на год
  - default_targets[pf_key, metric_code]
  - ops_targets[pf_key, metric_code]
  - ops_project_targets[pf_key, project_id, metric_code]

Теперь добавляем period_month TEXT в PK. Sentinel-значение '__default__'
означает «применяется ко всем месяцам» — это поведение текущих записей.
Все существующие строки получат '__default__' автоматически.

Логика выбора effective target в backend:
  monthly_specific  →  default-month  (если нет конкретного месяца)

Revision ID: 0014
Revises: 0013
"""
from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

SCHEMA = "pnl_service"
DEFAULT_SENTINEL = "__default__"

# Таблицы и их PK без period_month — переименованных колонок.
TABLES = [
    ("targets", ["planfact_key_id", "project_id", "metric_code"]),
    ("default_targets", ["planfact_key_id", "metric_code"]),
    ("ops_targets", ["planfact_key_id", "metric_code"]),
    ("ops_project_targets", ["planfact_key_id", "project_id", "metric_code"]),
]


def upgrade() -> None:
    """Добавить period_month, заполнить '__default__', включить в PK."""
    for table, old_pk in TABLES:
        # 1. Добавляем колонку с дефолтом — все existing rows = '__default__'.
        op.execute(f"""
            ALTER TABLE {SCHEMA}.{table}
            ADD COLUMN period_month TEXT NOT NULL DEFAULT '{DEFAULT_SENTINEL}'
        """)
        # 2. Перестраиваем PK с включением нового столбца.
        old_pk_name = f"{table}_pkey"
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DROP CONSTRAINT {old_pk_name}")
        new_pk_cols = ", ".join(old_pk + ["period_month"])
        op.execute(
            f"ALTER TABLE {SCHEMA}.{table} ADD PRIMARY KEY ({new_pk_cols})"
        )


def downgrade() -> None:
    """Удаляем period_month и возвращаем PK без него.

    ВНИМАНИЕ: если в таблице есть строки с period_month != '__default__',
    они будут утеряны (или вызовут duplicate-key conflict при PK rebuild).
    Поэтому сначала удаляем все month-specific записи.
    """
    for table, old_pk in TABLES:
        op.execute(
            f"DELETE FROM {SCHEMA}.{table} WHERE period_month <> '{DEFAULT_SENTINEL}'"
        )
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DROP CONSTRAINT {table}_pkey")
        op.execute(
            f"ALTER TABLE {SCHEMA}.{table} ADD PRIMARY KEY ({', '.join(old_pk)})"
        )
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DROP COLUMN period_month")
