"""Серверные сессии: создание, валидация, отзыв.

Token = 32 случайных байта в hex (64 символа). Кладётся в HttpOnly cookie
`pnl_session`. Сессия валидна до `expires_at`; при каждом валидном запросе
обновляем `last_seen_at` (rolling refresh — продлевает таймаут активности).

V8 (code-review 2026-06-10): в БД храним НЕ сырой токен, а его SHA-256
(hex). Read-доступ к БД (бэкап, дамп) больше не даёт угнать сессии.
Конвенция по слоям:
  - cookie ↔ юзер: сырой токен;
  - всё, что внутри sessions.py принимает `token` из cookie
    (get_session_with_user, delete_session) — хэширует само;
  - в `UserSession.token` и в `request.state.session_token` — хэш;
    функции, принимающие stored-токен (revoke_other_sessions keep_token),
    ждут именно хэш.
Сырой токен после login доступен один раз через `s.plain_token`.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import User, UserSession


# Токен живёт 30 дней. После expires_at — невалиден, требуется новый login.
SESSION_TTL_DAYS = 30


def _new_token() -> str:
    return secrets.token_hex(32)  # 64 hex-char


def hash_token(raw_token: str) -> str:
    """Сырой cookie-токен → хранимая форма (SHA-256 hex, 64 символа)."""
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_session(
    session: AsyncSession,
    user: User,
    *,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
) -> UserSession:
    """Завести новую сессию для уже верифицированного пользователя.

    В БД пишем hash; сырой токен (для Set-Cookie) — в `s.plain_token`,
    он существует только в памяти этого запроса."""
    token = _new_token()
    s = UserSession(
        token=hash_token(token),
        user_id=user.id,
        expires_at=_now() + timedelta(days=SESSION_TTL_DAYS),
        user_agent=user_agent,
        ip=ip,
    )
    s.plain_token = token  # не-column атрибут, в БД не попадает
    session.add(s)
    await session.flush()
    return s


async def get_session_with_user(
    session: AsyncSession, token: str
) -> Optional[UserSession]:
    """Найти сессию по токену + пред-загрузить связанного User.

    Возвращает None если:
    - токена нет в БД
    - сессия просрочена (expires_at < now)

    `token` — СЫРОЙ из cookie; ищем по его SHA-256.
    """
    if not token or len(token) != 64:
        return None
    stmt = (
        select(UserSession)
        .options(selectinload(UserSession.user))
        .where(UserSession.token == hash_token(token))
    )
    result = await session.execute(stmt)
    s = result.scalar_one_or_none()
    if s is None:
        return None
    if s.expires_at <= _now():
        # Просрочка — удаляем подметая (не страшно, что параллельно тот же
        # коннект будет читать; следующий get вернёт None).
        await session.delete(s)
        await session.flush()
        return None
    return s


async def touch_session(session: AsyncSession, s: UserSession) -> None:
    """Обновить last_seen_at + продлить expires_at (rolling refresh)."""
    s.last_seen_at = _now()
    s.expires_at = _now() + timedelta(days=SESSION_TTL_DAYS)
    await session.flush()


async def delete_session(session: AsyncSession, token: str) -> bool:
    """Удалить сессию по СЫРОМУ cookie-токену. Используется при logout."""
    stmt = delete(UserSession).where(UserSession.token == hash_token(token))
    result = await session.execute(stmt)
    return result.rowcount > 0


async def delete_session_by_stored(session: AsyncSession, stored_token: str) -> bool:
    """Удалить сессию по ХРАНИМОМУ хэшу (например, из list_sessions_for_user).
    Используется при отзыве конкретной сессии из UI."""
    stmt = delete(UserSession).where(UserSession.token == stored_token)
    result = await session.execute(stmt)
    return result.rowcount > 0


async def list_sessions_for_user(
    session: AsyncSession, user_id: int
) -> list[UserSession]:
    """Список активных сессий пользователя — для UI «Активные сессии»."""
    stmt = (
        select(UserSession)
        .where(UserSession.user_id == user_id)
        .where(UserSession.expires_at > _now())
        .order_by(UserSession.last_seen_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def revoke_other_sessions(
    session: AsyncSession, user_id: int, keep_token: str
) -> int:
    """Удалить все сессии юзера КРОМЕ переданной. Используется при смене пароля."""
    stmt = delete(UserSession).where(
        UserSession.user_id == user_id,
        UserSession.token != keep_token,
    )
    result = await session.execute(stmt)
    return result.rowcount
