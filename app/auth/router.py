"""HTTP-эндпоинты аутентификации: login / logout / me + профиль + интеграции.

Подключается в app/main.py через app.include_router(auth_router).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from . import audit
from .dependencies import SESSION_COOKIE, require_user
from .models import User
from .passwords import verify_password
from .ratelimit import login_limiter
from .sessions import (
    SESSION_TTL_DAYS,
    create_session,
    delete_session,
    list_sessions_for_user,
    revoke_other_sessions,
)
from .users import get_user_by_username, update_password, update_integrations


# Без общего префикса — login/logout/me живут под /auth/*, а профиль и
# интеграции — под /api/me/* (так согласовано с остальным API).
router = APIRouter(tags=["auth"])


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

@router.post("/auth/login", response_model=UserPublic)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    """Аутентификация. Успех → ставим HttpOnly cookie pnl_session.

    Rate-limit: 5 неудачных попыток / 15 мин / IP. Каждое событие пишется
    в audit_log."""
    ip = request.client.host if request.client else "unknown"

    allowed, retry_after = login_limiter.check(ip)
    if not allowed:
        await audit.log_audit(
            db, audit.ACTION_LOGIN_RATE_LIMITED,
            request=request,
            details={"username": body.username, "retry_after_sec": retry_after},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Слишком много неудачных попыток. Повторите через {retry_after // 60 + 1} мин.",
            headers={"Retry-After": str(retry_after)},
        )

    u = await get_user_by_username(db, body.username)
    if u is None or not verify_password(body.password, u.password_hash):
        login_limiter.record_failure(ip)
        await audit.log_audit(
            db, audit.ACTION_LOGIN_FAILED,
            user_id=(u.id if u else None),
            request=request,
            details={"username": body.username, "user_found": u is not None},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    login_limiter.reset(ip)
    s = await create_session(
        db, u,
        user_agent=request.headers.get("user-agent"),
        ip=ip if ip != "unknown" else None,
    )
    await audit.log_audit(
        db, audit.ACTION_LOGIN_SUCCESS,
        user_id=u.id, request=request,
    )

    response.set_cookie(
        key=SESSION_COOKIE, value=s.token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    return UserPublic.from_user(u)


@router.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    """Удалить серверную сессию + очистить cookie. 204 No Content."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await delete_session(db, token)
        await audit.log_audit(
            db, audit.ACTION_LOGOUT,
            request=request,
        )
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/me", response_model=UserPublic)
async def me(user: User = Depends(require_user)):
    """Текущий пользователь — для топбара и /settings."""
    return UserPublic.from_user(user)


# ---------- Профиль: смена пароля + сессии ----------

class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


@router.post("/api/me/password")
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Сменить свой пароль. Требует текущий пароль для подтверждения.
    Все остальные сессии этого юзера отзываются (текущая остаётся живой)."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текущий пароль неверен",
        )
    if body.current_password == body.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Новый пароль должен отличаться от текущего",
        )
    await update_password(session, user.id, body.new_password)
    # Отозвать все сессии кроме текущей — security best practice
    current_token = request.state.session_token if hasattr(request.state, "session_token") else None
    revoked = 0
    if current_token:
        revoked = await revoke_other_sessions(session, user.id, current_token)
    await audit.log_audit(
        session, audit.ACTION_PASSWORD_CHANGED,
        user_id=user.id, request=request,
        details={"other_sessions_revoked": revoked},
    )
    return {"status": "ok"}


class SessionPublic(BaseModel):
    token_short: str   # первые 8 hex для отображения, полный не отдаём
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: Optional[str]
    ip: Optional[str]
    is_current: bool


@router.get("/api/me/sessions", response_model=list[SessionPublic])
async def list_my_sessions(
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Активные сессии текущего юзера."""
    rows = await list_sessions_for_user(session, user.id)
    cur = getattr(request.state, "session_token", None)
    return [
        SessionPublic(
            token_short=s.token[:8],
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            expires_at=s.expires_at,
            user_agent=s.user_agent,
            ip=str(s.ip) if s.ip else None,
            is_current=(s.token == cur),
        )
        for s in rows
    ]


@router.delete("/api/me/sessions/{token_short}")
async def revoke_my_session(
    token_short: str,
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Отозвать одну из своих сессий по короткому prefix-токену.

    На фронте мы показываем только token_short (8 hex). Чтобы найти полный
    токен — ищем среди user_id-сессий тот, что начинается с prefix.
    """
    if len(token_short) < 6:
        raise HTTPException(400, "token_short слишком короткий")
    rows = await list_sessions_for_user(session, user.id)
    matches = [s for s in rows if s.token.startswith(token_short)]
    if len(matches) == 0:
        raise HTTPException(404, "Сессия не найдена")
    if len(matches) > 1:
        # Маловероятно (8 hex = 4 млрд комбинаций), но на всякий случай
        raise HTTPException(400, "Неоднозначный prefix — укажите больше символов")
    target = matches[0]
    cur = getattr(request.state, "session_token", None)
    if target.token == cur:
        raise HTTPException(
            400,
            "Нельзя отозвать текущую сессию — используйте кнопку «Выйти»",
        )
    await delete_session(session, target.token)
    await audit.log_audit(
        session, audit.ACTION_SESSION_REVOKED,
        user_id=user.id, request=request,
        details={"token_short": token_short},
    )
    return {"status": "ok"}


# ---------- Интеграции: PlanFact key + Dodo IS credentials_name ----------

class IntegrationsRequest(BaseModel):
    planfact_api_key: Optional[str] = None
    dodois_credentials_name: Optional[str] = None


@router.patch("/api/me/integrations")
async def patch_integrations(
    body: IntegrationsRequest,
    request: Request,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Обновить интеграции текущего пользователя. None = не менять,
    пустая строка = очистить."""
    await update_integrations(
        session, user.id,
        dodois_credentials_name=body.dodois_credentials_name,
        planfact_api_key=body.planfact_api_key,
    )
    await audit.log_audit(
        session, audit.ACTION_INTEGRATIONS_UPDATED,
        user_id=user.id, request=request,
        details={
            # Не пишем сами значения — только что меняли
            "planfact_changed": body.planfact_api_key is not None,
            "dodois_changed": body.dodois_credentials_name is not None,
        },
    )
    return {"status": "ok"}


class IntegrationStatus(BaseModel):
    """Маскированное представление текущих интеграций (без полного ключа)."""
    planfact_key_masked: Optional[str]   # '_pUi...RGXs' или None
    dodois_credentials_name: Optional[str]


# ---------- Audit log: свои события ----------

class AuditEntry(BaseModel):
    id: int
    action: str
    details: Optional[dict]
    ip: Optional[str]
    user_agent: Optional[str]
    created_at: datetime


@router.get("/api/me/audit", response_model=list[AuditEntry])
async def list_my_audit(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
):
    """Последние audit-события текущего юзера. Без чувствительных данных."""
    from sqlalchemy import select
    from .models import AuditLog
    stmt = (
        select(AuditLog)
        .where(AuditLog.user_id == user.id)
        .order_by(AuditLog.created_at.desc())
        .limit(min(max(limit, 1), 200))
    )
    result = await session.execute(stmt)
    return [
        AuditEntry(
            id=r.id, action=r.action, details=r.details,
            ip=str(r.ip) if r.ip else None,
            user_agent=r.user_agent, created_at=r.created_at,
        )
        for r in result.scalars()
    ]


@router.get("/api/me/integrations", response_model=IntegrationStatus)
async def get_integrations(user: User = Depends(require_user)):
    """Маска для текущих интеграций — UI показывает первые/последние символы."""
    pf = (user.planfact_api_key or "").strip()
    masked: Optional[str]
    if pf:
        masked = (pf[:4] + "..." + pf[-4:]) if len(pf) > 12 else "***"
    else:
        masked = None
    return IntegrationStatus(
        planfact_key_masked=masked,
        dodois_credentials_name=user.dodois_credentials_name,
    )
