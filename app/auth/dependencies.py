"""FastAPI-зависимости для аутентификации.

Главный паттерн в коде:
    @app.get("/api/whatever", dependencies=[Depends(require_user)])
    async def handler(user: User = Depends(require_user), ...):
        ...

Поведение:
- Cookie `pnl_session` с валидным токеном → request.state.user заполняется,
  ручка работает.
- Нет cookie / просрочена / удалена → 401 для /api/*; redirect на /login для
  HTML-роутов (это решает SessionAuthMiddleware ниже, не зависимости).
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from .models import User, UserSession
from .sessions import get_session_with_user, touch_session


SESSION_COOKIE = "pnl_session"


async def _resolve_user(
    request: Request,
    db: AsyncSession,
) -> Optional[tuple[UserSession, User]]:
    """Вытащить (session, user) из cookie. None если невалидно."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    s = await get_session_with_user(db, token)
    if s is None:
        return None
    # rolling refresh — обновляем last_seen_at
    await touch_session(db, s)
    return s, s.user


async def require_user(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> User:
    """Зависимость для всех защищённых роутов. Вернёт User или 401."""
    pair = await _resolve_user(request, db)
    if pair is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется аутентификация",
            headers={"X-Auth": "session-required"},
        )
    s, u = pair
    # Прокидываем в state, чтобы хендлеры могли достать без повторной зависимости
    request.state.user = u
    request.state.session_token = s.token
    return u


async def require_admin(user: User = Depends(require_user)) -> User:
    """Тот же, что require_user, но дополнительно проверяет is_admin."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нужны права администратора",
        )
    return user


async def optional_user(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> Optional[User]:
    """Не выкидывает 401, просто возвращает User или None.

    Используется для HTML-страниц, где middleware решает «пускать или редирект»
    отдельно (тут зависимость только для прокидывания user в шаблон).
    """
    pair = await _resolve_user(request, db)
    if pair is None:
        return None
    s, u = pair
    request.state.user = u
    request.state.session_token = s.token
    return u
