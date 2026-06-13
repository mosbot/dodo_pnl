"""S22: planfact_keys.live_revenue_from_dodois — выручка незакрытого месяца из Dodo IS.

Когда TRUE, для ТЕКУЩЕГО (live, незакрытого) полного месяца строка REVENUE
и разбивка по каналам берутся из Dodo IS (/finances/sales/units/monthly),
а не из PlanFact. Причина: PlanFact подтягивает продажи дня только в ~23:15,
плюс встречаются артефакты разнесения («Нераспределенный доход»). Закрытые
месяцы остаются на PlanFact (immutable). См. контекст в CLAUDE.md.

Деплой безопасен: default FALSE — поведение не меняется, пока флаг не включён
вручную (SQL) для конкретного ключа. Любой сбой Dodo → graceful fallback на
выручку PlanFact (страница не ломается).

Revision ID: 0027
Revises: 0026
"""
from alembic import op


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pnl_service.planfact_keys
        ADD COLUMN live_revenue_from_dodois BOOLEAN NOT NULL DEFAULT FALSE
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE pnl_service.planfact_keys "
        "DROP COLUMN live_revenue_from_dodois"
    )
