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
    """Любой администратор (super OR network). Используется legacy-роутами,
    которые сами скоупятся по user.planfact_key_id (например /api/template/*,
    /api/metrics/*, /api/projects/config) — для них network_admin не опаснее
    super_admin'а, т.к. он работает только в своём scope."""
    if not user.is_any_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нужны права администратора",
        )
    return user


async def require_super_admin(user: User = Depends(require_user)) -> User:
    """Только super_admin. Для глобальных операций: CRUD planfact_keys,
    смена is_admin_managed, доступ к чужим ключам."""
    if not user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нужны права супер-администратора",
        )
    return user


def require_admin_for_key(key_param: str = "key_id"):
    """Фабрика: разрешает super_admin'у работать с любым PF-ключом, а
    network_admin'у — только со своим (user.planfact_key_id == key_id).
    Используется в эндпойнтах вида /api/admin/planfact-keys/{key_id}/*.

    Применение:
        @router.patch("/api/admin/planfact-keys/{key_id}/...")
        async def handler(
            key_id: int,
            user: User = Depends(require_admin_for_key("key_id")),
        ): ...
    """
    async def _dep(
        request: Request,
        user: User = Depends(require_user),
    ) -> User:
        if user.is_super_admin:
            return user
        if not user.is_network_admin:
            raise HTTPException(403, "Нужны права администратора")
        # network_admin → проверяем принадлежность ключа
        target_key = request.path_params.get(key_param)
        try:
            target_key_id = int(target_key) if target_key is not None else None
        except (TypeError, ValueError):
            raise HTTPException(400, f"Bad {key_param}")
        if target_key_id != user.planfact_key_id:
            raise HTTPException(
                403, "Этот ключ вне области вашей сети",
            )
        return user
    return _dep


def require_admin_for_user(user_param: str = "user_id"):
    """Фабрика: super_admin может работать с любым юзером, network_admin —
    только с юзерами в своём planfact_key. Для CRUD по /api/admin/users/{id}/*.
    """
    async def _dep(
        request: Request,
        actor: User = Depends(require_user),
        db: AsyncSession = Depends(get_session),
    ) -> User:
        if actor.is_super_admin:
            return actor
        if not actor.is_network_admin:
            raise HTTPException(403, "Нужны права администратора")
        target = request.path_params.get(user_param)
        try:
            target_user_id = int(target) if target is not None else None
        except (TypeError, ValueError):
            raise HTTPException(400, f"Bad {user_param}")
        from .users import get_user_by_id
        target_user = await get_user_by_id(db, target_user_id)
        if target_user is None:
            raise HTTPException(404, "Пользователь не найден")
        if target_user.planfact_key_id != actor.planfact_key_id:
            raise HTTPException(
                403, "Этот пользователь вне области вашей сети",
            )
        return actor
    return _dep


def require_visibility_level(min_level: int, role_label: str = ""):
    """Фабрика зависимостей: разрешает доступ юзеру с visibility_level >=
    min_level. Админ (is_admin=True) проходит всегда — у него полный доступ.

    Используется для гейтинга редактирования целей и других «управленческих»
    операций, которые не нужно ограничивать строго админом, но не должны
    быть доступны управляющему пиццерией (visibility_level=10)."""
    async def _dep(user: User = Depends(require_user)) -> User:
        if user.is_admin:
            return user
        if (user.visibility_level or 0) < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Нужен уровень доступа ≥ {min_level}"
                    + (f" ({role_label})" if role_label else "")
                ),
            )
        return user
    return _dep


# Готовые алиасы. Числа соответствуют пресетам visibility_level,
# см. /settings → Пользователи: 10/30/60/100.
require_territorial = require_visibility_level(30, "Территориальный или выше")
require_director = require_visibility_level(60, "Директор или выше")
require_partner = require_visibility_level(100, "Партнёр")


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
