"""Серверные сессии: создание, валидация, отзыв.

Token = 32 случайных байта в hex (64 символа). Кладётся в HttpOnly cookie
`pnl_session`. Сессия валидна до `expires_at`; при каждом валидном запросе
обновляем `last_seen_at` (rolling refresh — продлевает таймаут активности).
"""
from __future__ import annotations

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_session(
    session: AsyncSession,
    user: User,
    *,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
) -> UserSession:
    """Завести новую сессию для уже верифицированного пользователя."""
    token = _new_token()
    s = UserSession(
        token=token,
        user_id=user.id,
        expires_at=_now() + timedelta(days=SESSION_TTL_DAYS),
        user_agent=user_agent,
        ip=ip,
    )
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
    """
    if not token or len(token) != 64:
        return None
    stmt = (
        select(UserSession)
        .options(selectinload(UserSession.user))
        .where(UserSession.token == token)
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
    """Удалить сессию по токену. Используется при logout."""
    stmt = delete(UserSession).where(UserSession.token == token)
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
