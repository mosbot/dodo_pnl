"""S15: разделение админа на super_admin / network_admin + projects_config.is_admin_managed.

Контекст: продаём доступ через Dodo Marketplace разным сетям. Один Super
Admin (Anthrey) обслуживает все сети, Network Admin (oltruist → Xfood)
работает только в рамках своего planfact_key.

Изменения:
  1. users.role VARCHAR(20) — заменяет is_admin (значения: super_admin /
     network_admin / user). Backfill: is_admin=true → 'super_admin'.
  2. projects_config.is_admin_managed BOOL DEFAULT TRUE — суперадмин решает
     какие проекты сети доступны её network-админу для управления.
     По умолчанию TRUE — backward-compat: всё текущее остаётся видимым.

Drop is_admin происходит в этой же миграции — все вызовы кода переезжают
на role property/проверку.

Revision ID: 0015
Revises: 0014
"""
from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. users.role
    op.execute("""
        ALTER TABLE pnl_service.users
        ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'
    """)
    op.execute("""
        UPDATE pnl_service.users
        SET role = 'super_admin'
        WHERE is_admin = true
    """)
    op.execute("""
        ALTER TABLE pnl_service.users
        ADD CONSTRAINT users_role_check
        CHECK (role IN ('super_admin', 'network_admin', 'user'))
    """)
    op.execute("""
        ALTER TABLE pnl_service.users
        DROP COLUMN is_admin
    """)

    # 2. projects_config.is_admin_managed
    op.execute("""
        ALTER TABLE pnl_service.projects_config
        ADD COLUMN is_admin_managed BOOLEAN NOT NULL DEFAULT TRUE
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE pnl_service.projects_config DROP COLUMN is_admin_managed")
    op.execute("ALTER TABLE pnl_service.users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT false")
    op.execute("UPDATE pnl_service.users SET is_admin = true WHERE role IN ('super_admin', 'network_admin')")
    op.execute("ALTER TABLE pnl_service.users DROP CONSTRAINT users_role_check")
    op.execute("ALTER TABLE pnl_service.users DROP COLUMN role")
