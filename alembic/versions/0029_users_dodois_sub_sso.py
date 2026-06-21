"""SSO: users.dodois_sub + password_hash опционален.

Для входа через Dodo IS (SSO) заводим pnl-юзеров, привязанных к Dodo-аккаунту
по `dodois_sub` (стабильный sub из sa), без локального пароля. Локальные юзеры
(созданные админом) продолжают логиниться по password_hash. Поэтому:
- ADD users.dodois_sub (nullable, unique — один pnl-юзер на Dodo-аккаунт);
- password_hash → nullable (SSO-юзеры без пароля; NULL-хеш не матчит argon2).

Revision ID: 0029
Revises: 0028
"""
from alembic import op


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pnl_service.users ADD COLUMN dodois_sub VARCHAR(64)")
    op.execute(
        "CREATE UNIQUE INDEX ix_users_dodois_sub "
        "ON pnl_service.users (dodois_sub)"
    )
    op.execute(
        "ALTER TABLE pnl_service.users ALTER COLUMN password_hash DROP NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE pnl_service.users ALTER COLUMN password_hash SET NOT NULL"
    )
    op.execute("DROP INDEX IF EXISTS pnl_service.ix_users_dodois_sub")
    op.execute("ALTER TABLE pnl_service.users DROP COLUMN dodois_sub")
