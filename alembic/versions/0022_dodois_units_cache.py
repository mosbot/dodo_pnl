"""S18: dodois_units_cache — кэш названий пиццерий из /auth/roles/units.

Имена пиццерий меняются крайне редко (новая точка — раз в месяцы), но
fetch_units сейчас вызывается на каждый /api/board, что добавляет
лишний round-trip. PK по uuid: имя универсально для всех тенантов
(Кубинка-1 это Кубинка-1 у кого бы ни был доступ).

TTL логика — в коде; запись `refreshed_at` нужна чтобы понимать когда
последний раз обновляли. Сама таблица никогда не expire'ит — старые
имена остаются как fallback на случай если fetch_units упадёт.

Revision ID: 0022
Revises: 0021
"""
from alembic import op


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pnl_service.dodois_units_cache (
            uuid          VARCHAR(64) NOT NULL PRIMARY KEY,
            name          VARCHAR(255) NOT NULL,
            refreshed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE pnl_service.dodois_units_cache")
