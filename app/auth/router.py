"""HTTP-эндпоинты аутентификации: login / logout / me + профиль + интеграции.

Подключается в app/main.py через app.include_router(auth_router).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..planfact import invalidate_planfact_for
from . import audit
from .dependencies import SESSION_COOKIE, require_admin, require_user
from .models import User
from .passwords import verify_password
from .ratelimit import login_limiter, username_limiter
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
    role: str                          # 'super_admin' / 'network_admin' / 'user'
    is_admin: bool                     # legacy для существующего фронта
    visibility_level: int
    has_dodois_credentials: bool
    has_planfact_key: bool
    dodois_linked: bool                # привязан ли вход через Dodo IS (SSO)
    has_password: bool                 # есть ли локальный пароль
    capabilities: Optional[list[str]] = None  # лицензии тенанта (None = неизвестно)

    @classmethod
    def from_user(cls, u: User) -> "UserPublic":
        return cls(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            role=u.role,
            is_admin=u.is_any_admin,
            visibility_level=u.visibility_level,
            has_dodois_credentials=bool(u.dodois_credentials_name),
            has_planfact_key=bool(u.planfact_key_id),
            dodois_linked=bool(getattr(u, "dodois_sub", None)),
            has_password=bool(u.password_hash),
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
    uname_key = body.username.strip().lower()

    allowed, retry_after = login_limiter.check(ip)
    if allowed:
        # V14: второй лимит по username — защита от распределённого
        # brute-force одного аккаунта со многих IP.
        allowed, retry_after = username_limiter.check(uname_key)
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
        username_limiter.record_failure(uname_key)
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
    username_limiter.reset(uname_key)
    s = await create_session(
        db, u,
        user_agent=request.headers.get("user-agent"),
        ip=ip if ip != "unknown" else None,
    )
    await audit.log_audit(
        db, audit.ACTION_LOGIN_SUCCESS,
        user_id=u.id, request=request,
    )

    # samesite=strict — защищает от CSRF (cookie не отправляется при
    # cross-site навигации, в т.ч. <form method=POST>). Минус — переход
    # из внешнего письма/чата на /api/* теряет cookie, но для внутреннего
    # дашборда это приемлемо: пользователь начинает с /login и получает
    # cookie заново.
    response.set_cookie(
        key=SESSION_COOKIE, value=s.plain_token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True, secure=True, samesite="strict", path="/",
    )
    return UserPublic.from_user(u)


@router.get("/auth/sso")
async def auth_sso(
    request: Request,
    next: str = "/",
    db: AsyncSession = Depends(get_session),
):
    """SSO-вход через sa: общая кука .dodotool.ru → sa /me → pnl-сессия.
    Новый тенант провижится автоматически при наличии лицензии pnl (JIT)."""
    from . import sso as sso_mod
    from ..config import settings

    sa_cookie = request.cookies.get(sso_mod.SA_COOKIE_NAME)
    if not sa_cookie:
        # Нет сессии sa → на OAuth-вход sa с возвратом на /auth/sso.
        if settings.sa_login_url and settings.public_base_url:
            rt = settings.public_base_url.rstrip("/") + "/auth/sso"
            return RedirectResponse(
                f"{settings.sa_login_url}?return_to={rt}", status_code=302,
            )
        return RedirectResponse("/login?sso=nosession", status_code=302)
    sa_user = await sso_mod.resolve_sa_user(sa_cookie)
    if not sa_user:
        return RedirectResponse("/login?sso=invalid", status_code=302)
    u, status = await sso_mod.get_or_provision_user(
        db, sa_cookie, sa_user["sub"], sa_user["name"],
    )
    if u is None:
        # status: "request" — тенант сети есть, можно запросить доступ админу;
        #         "nosub" — ни тенанта, ни лицензии pnl.
        return RedirectResponse(f"/login?sso={status}", status_code=302)

    ip = request.client.host if request.client else None
    s = await create_session(
        db, u, user_agent=request.headers.get("user-agent"), ip=ip,
    )
    await audit.log_audit(
        db, audit.ACTION_LOGIN_SUCCESS, user_id=u.id, request=request,
    )
    await db.commit()
    dest = next if (next.startswith("/") and not next.startswith("//")) else "/"
    resp = RedirectResponse(dest, status_code=302)
    resp.set_cookie(
        key=SESSION_COOKIE, value=s.plain_token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    return resp


@router.get("/auth/link/start")
async def auth_link_start(user: User = Depends(require_user)):
    """Начать привязку Dodo IS к текущему аккаунту: OAuth sa → возврат на /auth/link."""
    from ..config import settings
    if not (settings.sa_login_url and settings.public_base_url):
        return RedirectResponse("/settings?link=unavailable", status_code=302)
    rt = settings.public_base_url.rstrip("/") + "/auth/link"
    return RedirectResponse(f"{settings.sa_login_url}?return_to={rt}", status_code=302)


@router.get("/auth/link")
async def auth_link(
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_session),
):
    """Привязать Dodo-аккаунт (из sa-сессии) к текущему pnl-юзеру.

    Юзер подтверждает владение Dodo-аккаунтом, пройдя OAuth; берём его sub из
    sa /me и биндим к текущему (залогиненному локально) аккаунту."""
    from sqlalchemy import select
    from . import sso as sso_mod

    sa_cookie = request.cookies.get(sso_mod.SA_COOKIE_NAME)
    if not sa_cookie:
        return RedirectResponse("/settings?link=nosession", status_code=302)
    sa_user = await sso_mod.resolve_sa_user(sa_cookie)
    if not sa_user:
        return RedirectResponse("/settings?link=invalid", status_code=302)
    sub = sa_user["sub"]
    other = (await db.execute(
        select(User).where(User.dodois_sub == sub)
    )).scalar_one_or_none()
    if other is not None and other.id != user.id:
        return RedirectResponse("/settings?link=taken", status_code=302)
    db_user = await db.get(User, user.id)
    db_user.dodois_sub = sub
    await db.commit()
    return RedirectResponse("/settings?link=ok", status_code=302)


class SsoLinkRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/auth/sso-link", response_model=UserPublic)
async def sso_link(
    body: SsoLinkRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    """Привязка «с нашей стороны»: SSO уже выполнен (sub из активной sa-сессии),
    но pnl-аккаунта по этому Dodo нет. Юзер вводит локальные логин+пароль —
    проверяем, биндим dodois_sub к его аккаунту и логиним. Инициируется прямо
    с экрана входа (?sso=noaccount). sub берём ТОЛЬКО из валидной sa-сессии,
    не из тела запроса."""
    from sqlalchemy import select
    from . import sso as sso_mod

    sa_cookie = request.cookies.get(sso_mod.SA_COOKIE_NAME)
    if not sa_cookie:
        raise HTTPException(400, "Нет активной сессии Dodo IS — войдите через Dodo IS заново.")
    sa_user = await sso_mod.resolve_sa_user(sa_cookie)
    if not sa_user:
        raise HTTPException(400, "Сессия Dodo IS недействительна.")
    sub = sa_user["sub"]

    # Rate-limit как у обычного логина (защита от брутфорса пароля).
    ip = request.client.host if request.client else "unknown"
    uname_key = body.username.strip().lower()
    allowed, retry_after = login_limiter.check(ip)
    if allowed:
        allowed, retry_after = username_limiter.check(uname_key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Слишком много попыток. Повторите через {retry_after // 60 + 1} мин.",
            headers={"Retry-After": str(retry_after)},
        )

    u = await get_user_by_username(db, body.username)
    if u is None or not verify_password(body.password, u.password_hash):
        login_limiter.record_failure(ip)
        username_limiter.record_failure(uname_key)
        await audit.log_audit(
            db, audit.ACTION_LOGIN_FAILED, user_id=(u.id if u else None),
            request=request, details={"username": body.username, "sso_link": True},
        )
        await db.commit()
        raise HTTPException(401, "Неверный логин или пароль")
    login_limiter.reset(ip)
    username_limiter.reset(uname_key)

    db_user = await db.get(User, u.id)
    if db_user.dodois_sub and db_user.dodois_sub != sub:
        raise HTTPException(409, "Этот аккаунт уже привязан к другому Dodo IS.")
    other = (await db.execute(
        select(User).where(User.dodois_sub == sub)
    )).scalar_one_or_none()
    if other is not None and other.id != db_user.id:
        raise HTTPException(409, "Этот аккаунт Dodo IS уже привязан к другому логину.")

    db_user.dodois_sub = sub
    s = await create_session(
        db, db_user, user_agent=request.headers.get("user-agent"),
        ip=ip if ip != "unknown" else None,
    )
    await audit.log_audit(
        db, audit.ACTION_LOGIN_SUCCESS, user_id=db_user.id, request=request,
        details={"sso_link": True},
    )
    await db.commit()
    response.set_cookie(
        key=SESSION_COOKIE, value=s.plain_token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True, secure=True, samesite="lax", path="/",
    )
    return UserPublic.from_user(db_user)


@router.post("/auth/unlink")
async def auth_unlink(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_session),
):
    """Отвязать Dodo IS от текущего аккаунта (останется вход по паролю)."""
    db_user = await db.get(User, user.id)
    if db_user.password_hash is None:
        raise HTTPException(
            status_code=400,
            detail="Нельзя отвязать: у аккаунта нет пароля (вход только через Dodo IS).",
        )
    db_user.dodois_sub = None
    await db.commit()
    return {"status": "ok"}


# ---------- Запрос доступа (Dodo IS-юзер без локального аккаунта) ----------

@router.post("/auth/access-request")
async def auth_access_request(
    request: Request, db: AsyncSession = Depends(get_session),
):
    """Запрос доступа с экрана `?sso=request`. `sub` — ТОЛЬКО из валидной
    sa-сессии. Резолвим тенант сети по юнитам и создаём pending для его админа."""
    from . import access_requests as ar
    from . import sso as sso_mod

    sa_cookie = request.cookies.get(sso_mod.SA_COOKIE_NAME)
    if not sa_cookie:
        raise HTTPException(400, "Нет активной сессии Dodo IS — войдите заново.")
    sa_user = await sso_mod.resolve_sa_user(sa_cookie)
    if not sa_user:
        raise HTTPException(400, "Сессия Dodo IS недействительна.")
    sub, name = sa_user["sub"], sa_user["name"]

    already = (await db.execute(
        select(User).where(User.dodois_sub == sub)
    )).scalar_one_or_none()
    if already is not None:
        return {"status": "linked"}  # уже привязан — запрос не нужен

    all_units = await sso_mod._all_units(sa_cookie)
    uuids = [x.get("dodois_uuid") for x in all_units if x.get("dodois_uuid")]
    tenant_id = await ar.find_tenant_by_units(db, uuids)
    if tenant_id is None:
        raise HTTPException(404, "Сеть не найдена. Подписка на сервис не найдена.")
    units_snap = [
        {"uuid": x.get("dodois_uuid"), "name": x.get("name")}
        for x in all_units if x.get("dodois_uuid")
    ]
    req = await ar.create_request(
        db, planfact_key_id=tenant_id, dodois_sub=sub,
        name=name, email=None, units=units_snap,
    )
    await db.commit()
    return {"status": "pending", "id": req.id}


class AccessRequestOut(BaseModel):
    id: int
    dodois_sub: str
    name: Optional[str]
    email: Optional[str]
    units: Optional[list]
    created_at: datetime


@router.get("/api/admin/access-requests", response_model=list[AccessRequestOut])
async def list_access_requests(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    """Pending-запросы доступа для тенанта админа (network_admin/super_admin).
    Имена заведений резолвим при показе из кэша Dodo IS (в снимке — uuid)."""
    from .. import store
    from . import access_requests as ar
    if not admin.planfact_key_id:
        return []
    rows = await ar.list_pending_for_key(db, admin.planfact_key_id)

    def _norm(u):
        return (u or "").lower().replace("-", "")

    cache = await store.get_units_cache(db)
    name_by = {k: (v or {}).get("name") for k, v in cache.items()}

    def _units(snap):
        out = []
        for u in (snap or []):
            uid = u.get("uuid") if isinstance(u, dict) else None
            stored = u.get("name") if isinstance(u, dict) else None
            nm = (name_by.get(_norm(uid)) if uid else None) or stored \
                or (_norm(uid)[:8] if uid else "—")
            out.append({"uuid": uid, "name": nm})
        return out

    return [
        AccessRequestOut(
            id=r.id, dodois_sub=r.dodois_sub, name=r.name, email=r.email,
            units=_units(r.units), created_at=r.created_at,
        )
        for r in rows
    ]


class ApproveRequest(BaseModel):
    # «manager, кроме network_admin» = role=user + уровень видимости.
    visibility_level: int = Field(default=10, ge=0, le=100)


@router.post("/api/admin/access-requests/{req_id}/approve", response_model=UserPublic)
async def approve_access_request(
    req_id: int,
    body: ApproveRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    """Одобрить: создать User (role=user + visibility) с привязкой dodois_sub.
    network_admin создавать через этот поток НЕЛЬЗЯ."""
    from . import access_requests as ar
    req = await ar.get_request(db, req_id)
    if req is None or req.status != "pending":
        raise HTTPException(404, "Запрос не найден или уже обработан.")
    if req.planfact_key_id != admin.planfact_key_id:
        raise HTTPException(403, "Запрос относится к другому тенанту.")
    # Не плодим дубль по уже занятому sub.
    taken = (await db.execute(
        select(User).where(User.dodois_sub == req.dodois_sub)
    )).scalar_one_or_none()
    if taken is not None:
        await ar.mark_decided(db, req, status="approved", decided_by=admin.id)
        await db.commit()
        return UserPublic.from_user(taken)
    new_user = User(
        username=f"sso-{req.dodois_sub[:12]}",
        password_hash=None,
        display_name=req.name or f"Сотрудник {req.dodois_sub[:6]}",
        dodois_sub=req.dodois_sub,
        role="user",
        visibility_level=body.visibility_level,
        planfact_key_id=req.planfact_key_id,
    )
    db.add(new_user)
    await db.flush()
    await ar.mark_decided(db, req, status="approved", decided_by=admin.id)
    await audit.log_audit(
        db, audit.ACTION_ADMIN_USER_CREATED, user_id=admin.id,
        details={"access_request": req_id, "new_user": new_user.id, "via": "sso"},
    )
    await db.commit()
    return UserPublic.from_user(new_user)


@router.post("/api/admin/access-requests/{req_id}/deny", status_code=204)
async def deny_access_request(
    req_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    """Отклонить запрос доступа."""
    from . import access_requests as ar
    req = await ar.get_request(db, req_id)
    if req is None or req.status != "pending":
        raise HTTPException(404, "Запрос не найден или уже обработан.")
    if req.planfact_key_id != admin.planfact_key_id:
        raise HTTPException(403, "Запрос относится к другому тенанту.")
    await ar.mark_decided(db, req, status="denied", decided_by=admin.id)
    await db.commit()


@router.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    """Удалить серверную сессию + очистить cookie. 204 No Content."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        # Получим user_id до удаления сессии — нужен, чтобы выкинуть
        # PlanFact-клиента (с его HTTP-коннектами и кэшем) из памяти.
        from .sessions import get_session_with_user
        sess = await get_session_with_user(db, token)
        user_id = sess.user_id if sess else None
        await delete_session(db, token)
        await audit.log_audit(
            db, audit.ACTION_LOGOUT,
            request=request,
        )
        if user_id is not None:
            invalidate_planfact_for(user_id)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/me", response_model=UserPublic)
async def me(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_session),
):
    """Текущий пользователь — для топбара и /settings. Включает caps тенанта
    (лицензии из sa) для гейтинга сервисов во фронте; None = неизвестно."""
    pub = UserPublic.from_user(user)
    from ..licensing import get_tenant_capabilities
    caps = await get_tenant_capabilities(db, user.planfact_key_id)
    if caps is not None:
        pub.capabilities = sorted(caps)
    return pub


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
    # target.token — хранимый хэш (V8), удаляем by-stored.
    from .sessions import delete_session_by_stored
    await delete_session_by_stored(session, target.token)
    await audit.log_audit(
        session, audit.ACTION_SESSION_REVOKED,
        user_id=user.id, request=request,
        details={"token_short": token_short},
    )
    return {"status": "ok"}


# ---------- Интеграции: read-only для пользователя ----------
# Менять интеграции теперь может только админ через /api/admin/users/{id}.
# Сам юзер видит только что у него назначено.

class IntegrationStatus(BaseModel):
    """Read-only представление текущих интеграций. Сам api_key не отдаём —
    деталь каталога, доступная только админу."""
    planfact_key_id: Optional[int]
    planfact_key_name: Optional[str]
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
async def get_integrations(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Текущие интеграции пользователя. Только метаданные (имя ключа)."""
    from .models import PlanfactKey
    key_name: Optional[str] = None
    if user.planfact_key_id:
        pk = await session.get(PlanfactKey, user.planfact_key_id)
        if pk:
            key_name = pk.name
    return IntegrationStatus(
        planfact_key_id=user.planfact_key_id,
        planfact_key_name=key_name,
        dodois_credentials_name=user.dodois_credentials_name,
    )
