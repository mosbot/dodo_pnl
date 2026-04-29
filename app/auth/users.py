"""CRUD-операции над User.

Все функции принимают AsyncSession — вызвавший управляет транзакцией. Здесь
только запросы и базовая бизнес-логика (хеширование пароля, проверки уникальности).

Намеренно не возвращаем хеши паролей наружу — UserPublic-DTO будет в S1.3.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User
from .passwords import hash_password


async def get_user_by_id(session: AsyncSession, user_id: int) -> Optional[User]:
    return await session.get(User, user_id)


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    """Регистронечувствительный поиск (username хранится как-есть, но сравниваем
    через LOWER на обеих сторонах, чтобы admin == Admin == ADMIN)."""
    stmt = select(User).where(User.username == username.lower())
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_users(session: AsyncSession) -> list[User]:
    stmt = select(User).order_by(User.id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    display_name: Optional[str] = None,
    is_admin: bool = False,
    visibility_level: int = 100,
    dodois_credentials_name: Optional[str] = None,
    planfact_key_id: Optional[int] = None,
) -> User:
    """Создать пользователя. Username нормализуется к нижнему регистру.

    Бросает IntegrityError если username уже занят — ловить в вызывающей точке.
    """
    user = User(
        username=username.lower(),
        password_hash=hash_password(password),
        display_name=display_name,
        is_admin=is_admin,
        visibility_level=visibility_level,
        dodois_credentials_name=dodois_credentials_name,
        planfact_key_id=planfact_key_id,
    )
    session.add(user)
    await session.flush()
    return user


async def update_password(
    session: AsyncSession, user_id: int, new_password: str
) -> None:
    """Сменить пароль и обновить updated_at."""
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(
            password_hash=hash_password(new_password),
            updated_at=datetime.now(timezone.utc),
        )
    )
    await session.execute(stmt)


async def update_integrations(
    session: AsyncSession,
    user_id: int,
    *,
    dodois_credentials_name: Optional[str] = None,
    planfact_key_id: Optional[int] = None,
    clear_planfact_key: bool = False,
) -> None:
    """Обновить интеграционные настройки. None = не трогать. Для очистки
    привязки к PF-ключу передать clear_planfact_key=True (planfact_key_id
    не может различить «не передан» и «снять привязку», т.к. оба None).

    dodois_credentials_name: пустая строка/None обрабатывается на уровне UI.
    """
    values: dict = {"updated_at": datetime.now(timezone.utc)}
    if dodois_credentials_name is not None:
        values["dodois_credentials_name"] = dodois_credentials_name or None
    if clear_planfact_key:
        values["planfact_key_id"] = None
    elif planfact_key_id is not None:
        values["planfact_key_id"] = planfact_key_id
    if len(values) == 1:
        return  # нечего обновлять
    stmt = update(User).where(User.id == user_id).values(**values)
    await session.execute(stmt)


async def set_admin(session: AsyncSession, user_id: int, is_admin: bool) -> None:
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(is_admin=is_admin, updated_at=datetime.now(timezone.utc))
    )
    await session.execute(stmt)
