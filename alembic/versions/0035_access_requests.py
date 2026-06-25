"""access_requests — запрос доступа от Dodo IS-юзера без локального аккаунта.

SSO-вход прошёл, но `dodois_sub` не привязан, а тенант сети уже существует.
Юзер запрашивает доступ → pending-строка для planfact_key сети. Сетевой админ
одобряет (выбор уровня видимости) → создаётся User с привязкой sub. Один pending
на (planfact_key, dodois_sub).

Revision ID: 0035
Revises: 0034
"""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pnl_service.access_requests (
            id              BIGSERIAL PRIMARY KEY,
            planfact_key_id BIGINT NOT NULL
                            REFERENCES pnl_service.planfact_keys(id) ON DELETE CASCADE,
            dodois_sub      VARCHAR(64) NOT NULL,
            name            TEXT,
            email           TEXT,
            units           JSONB,
            status          VARCHAR(16) NOT NULL DEFAULT 'pending',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_by      BIGINT REFERENCES pnl_service.users(id) ON DELETE SET NULL,
            decided_at      TIMESTAMPTZ
        )
    """)
    op.execute(
        "CREATE INDEX ix_access_requests_pf "
        "ON pnl_service.access_requests(planfact_key_id)"
    )
    op.execute(
        "CREATE INDEX ix_access_requests_sub "
        "ON pnl_service.access_requests(dodois_sub)"
    )
    op.execute(
        "CREATE INDEX ix_access_requests_status "
        "ON pnl_service.access_requests(status)"
    )
    # Один активный (pending) запрос на (тенант, sub).
    op.execute(
        "CREATE UNIQUE INDEX uq_access_requests_pending "
        "ON pnl_service.access_requests(planfact_key_id, dodois_sub) "
        "WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE pnl_service.access_requests")
