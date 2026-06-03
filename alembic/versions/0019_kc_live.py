"""S16.3: KC_LIVE — расчётный Kitchen Cost из Dodo IS incentives.

Идея: PF-строка KC обновляется когда франчайзи занесёт расходы в учёт,
часто с задержкой неделя+. Параллельно тянем из Dodo IS
`/staff/incentives-by-members` — там по фактически закрытым сменам уже
есть `totalWage`. Считаем сумму по whitelisted должностям и делим на
выручку. Получаем «KC по факту смен», обновляющийся live.

Налоги по решению не накладываем — берём как есть net wage.

Изменения:
- `ops_metrics.kc_live_pct` REAL — рассчитанный KC% за период
- `planfact_keys.kc_kitchen_positions` JSONB DEFAULT '[]'
  Список должностей (строк) считающихся «кухонными». Пустой = используем
  defaults в коде (Пиццамейкер, Кассир, Менеджер смены, Управляющий).

Revision ID: 0019
Revises: 0018
"""
from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        ADD COLUMN kc_live_pct REAL
    """)
    op.execute("""
        ALTER TABLE pnl_service.planfact_keys
        ADD COLUMN kc_kitchen_positions JSONB NOT NULL DEFAULT '[]'::jsonb
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.planfact_keys
        DROP COLUMN kc_kitchen_positions
    """)
    op.execute("""
        ALTER TABLE pnl_service.ops_metrics
        DROP COLUMN kc_live_pct
    """)
