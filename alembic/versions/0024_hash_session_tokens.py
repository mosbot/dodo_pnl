"""V8 (code-review 2026-06-10): session-токены в БД → SHA-256.

До этой миграции в pnl_service.sessions.token лежал СЫРОЙ cookie-токен:
read-доступ к БД (бэкап, дамп, соседний сервис в том же Postgres) позволял
угнать любую активную сессию. Теперь храним sha256(token) hex.

Существующие сессии конвертируем in-place — cookie у юзеров остаются
валидными, т.к. lookup в app/auth/sessions.py хэширует cookie-значение
перед SELECT.

Миграция ОДНОНАПРАВЛЕННАЯ: получить сырой токен из хэша нельзя.
downgrade удаляет все сессии (юзеры просто перелогинятся).

Revision ID: 0024
Revises: 0023
"""
from alembic import op


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG 11+: встроенный sha256(bytea). Запускается ровно один раз —
    # повторный прогон двойным хэшированием не грозит (alembic).
    op.execute("""
        UPDATE pnl_service.sessions
        SET token = encode(sha256(convert_to(token, 'UTF8')), 'hex')
    """)


def downgrade() -> None:
    # Хэш необратим — единственный честный откат: сбросить все сессии.
    op.execute("DELETE FROM pnl_service.sessions")
