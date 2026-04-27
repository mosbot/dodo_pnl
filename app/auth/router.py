"""HTTP-эндпоинты аутентификации: login / logout / me.

Подключается в app/main.py через app.include_router(auth_router).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from .dependencies import SESSION_COOKIE, require_user
from .models import User
from .passwords import verify_password
from .sessions import (
    SESSION_TTL_DAYS,
    create_session,
    delete_session,
)
from .users import get_user_by_username


router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- DTO ----------

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserPublic(BaseModel):
    """Публичное представление User — без пароля и без чувствительных полей."""
    id: int
    username: str
    display_name: Optional[str]
    is_admin: bool
    has_dodois_credentials: bool
    has_planfact_key: bool

    @classmethod
    def from_user(cls, u: User) -> "UserPublic":
        return cls(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            is_admin=u.is_admin,
            has_dodois_credentials=bool(u.dodois_credentials_name),
            has_planfact_key=bool(u.planfact_api_key),
        )


# ---------- endpoints ----------

@router.post("/login", response_model=UserPublic)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    """Аутентификация. Успех → ставим HttpOnly cookie pnl_session, возвращаем user."""
    u = await get_user_by_username(db, body.username)
    if u is None or not verify_password(body.password, u.password_hash):
        # Намеренно не различаем «нет такого юзера» и «неверный пароль» —
        # один и тот же 401, чтобы не давать enumeration.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    s = await create_session(
        db, u,
        user_agent=request.headers.get("user-agent"),
        ip=(request.client.host if request.client else None),
    )

    # Cookie: HttpOnly + Secure + SameSite=Lax + Path=/
    # max_age в секундах
    response.set_cookie(
        key=SESSION_COOKIE,
        value=s.token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return UserPublic.from_user(u)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    """Удалить серверную сессию + очистить cookie. 204 No Content."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await delete_session(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserPublic)
async def me(user: User = Depends(require_user)):
    """Текущий пользователь — для топбара и /settings."""
    return UserPublic.from_user(user)
