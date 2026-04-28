"""Admin-эндпоинты: CRUD пользователей + cross-user управление проектами.

Все ручки гейтятся через Depends(require_admin) — non-admin получает 403.
Префикс /api/admin/* — отделён от собственных /api/me/*, чтобы было видно
из URL что это админ-операция.
"""
from __future__ import annotations

import secrets
import string
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import store
from ..db import get_session
from . import audit
from .dependencies import require_admin
from .models import User
from .users import (
    create_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    set_admin,
    update_integrations,
    update_password,
)


admin_router = APIRouter(tags=["admin"])


# ---------- DTO ----------

class AdminUserPublic(BaseModel):
    id: int
    username: str
    display_name: Optional[str]
    is_admin: bool
    dodois_credentials_name: Optional[str]
    planfact_key_masked: Optional[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_user(cls, u: User) -> "AdminUserPublic":
        pf = (u.planfact_api_key or "").strip()
        masked = (pf[:4] + "..." + pf[-4:]) if len(pf) > 12 else ("***" if pf else None)
        return cls(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            is_admin=u.is_admin,
            dodois_credentials_name=u.dodois_credentials_name,
            planfact_key_masked=masked,
            created_at=u.created_at.isoformat(),
            updated_at=u.updated_at.isoformat(),
        )


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    display_name: Optional[str] = None
    is_admin: bool = False
    dodois_credentials_name: Optional[str] = None
    planfact_api_key: Optional[str] = None


class AdminUserUpdate(BaseModel):
    display_name: Optional[str] = None
    is_admin: Optional[bool] = None
    dodois_credentials_name: Optional[str] = None
    planfact_api_key: Optional[str] = None


class ResetPasswordResponse(BaseModel):
    password: str
    detail: str = "Передайте этот пароль пользователю — повторно его узнать нельзя."


def _gen_password(n: int = 16) -> str:
    """Без спецсимволов в начале/конце, чтобы copy-paste не съел."""
    alpha = string.ascii_letters + string.digits
    return "".join(secrets.choice(alpha) for _ in range(n))


# ---------- CRUD users ----------

@admin_router.get("/api/admin/users", response_model=list[AdminUserPublic])
async def admin_list_users(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    users = await list_users(session)
    return [AdminUserPublic.from_user(u) for u in users]


@admin_router.post("/api/admin/users", response_model=AdminUserPublic)
async def admin_create_user(
    body: AdminUserCreate,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        u = await create_user(
            session,
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            is_admin=body.is_admin,
            dodois_credentials_name=body.dodois_credentials_name,
            planfact_api_key=body.planfact_api_key,
        )
        await audit.log_audit(
            session, audit.ACTION_ADMIN_USER_CREATED,
            user_id=admin.id, request=request,
            details={"target_user_id": u.id, "target_username": u.username, "is_admin": u.is_admin},
        )
        await session.commit()
        return AdminUserPublic.from_user(u)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Пользователь {body.username!r} уже существует",
        )


@admin_router.patch("/api/admin/users/{user_id}", response_model=AdminUserPublic)
async def admin_update_user(
    user_id: int,
    body: AdminUserUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")

    if body.is_admin is False and admin.id == u.id:
        raise HTTPException(400, "Нельзя снять админ-флаг с самого себя")

    fields_changed: list[str] = []
    if body.display_name is not None:
        u.display_name = body.display_name or None
        fields_changed.append("display_name")
    if body.is_admin is not None:
        u.is_admin = bool(body.is_admin)
        fields_changed.append("is_admin")
    if body.dodois_credentials_name is not None or body.planfact_api_key is not None:
        await update_integrations(
            session, u.id,
            dodois_credentials_name=body.dodois_credentials_name,
            planfact_api_key=body.planfact_api_key,
        )
        if body.dodois_credentials_name is not None:
            fields_changed.append("dodois_credentials_name")
        if body.planfact_api_key is not None:
            fields_changed.append("planfact_api_key")

    await audit.log_audit(
        session, audit.ACTION_ADMIN_USER_UPDATED,
        user_id=admin.id, request=request,
        details={"target_user_id": u.id, "target_username": u.username,
                 "fields_changed": fields_changed},
    )
    await session.flush()
    await session.commit()
    fresh = await get_user_by_id(session, user_id)
    return AdminUserPublic.from_user(fresh)


@admin_router.post(
    "/api/admin/users/{user_id}/reset-password", response_model=ResetPasswordResponse
)
async def admin_reset_password(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")
    new_pwd = _gen_password(16)
    await update_password(session, u.id, new_pwd)
    await audit.log_audit(
        session, audit.ACTION_ADMIN_PASSWORD_RESET,
        user_id=admin.id, request=request,
        details={"target_user_id": u.id, "target_username": u.username},
    )
    await session.commit()
    return ResetPasswordResponse(password=new_pwd)


@admin_router.delete("/api/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if user_id == admin.id:
        raise HTTPException(400, "Нельзя удалить самого себя")
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")
    target_username = u.username
    await session.delete(u)
    await audit.log_audit(
        session, audit.ACTION_ADMIN_USER_DELETED,
        user_id=admin.id, request=request,
        # user_id целевого юзера уже невалиден после delete — пишем только snapshot
        details={"target_user_id": user_id, "target_username": target_username},
    )
    await session.commit()
    return {"status": "ok"}


# ---------- Cross-user projects management ----------

class AdminProjectConfigUpdate(BaseModel):
    """Подмножество полей projects_config для админ-обновления чужого юзера."""
    is_active: Optional[bool] = None
    display_name: Optional[str] = None
    sort_order: Optional[int] = None
    dodo_unit_uuid: Optional[str] = None


@admin_router.get("/api/admin/users/{user_id}/projects-config")
async def admin_user_projects_config(
    user_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Текущие projects_config-переопределения для конкретного юзера."""
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")
    cfg = await store.list_projects_config(session, u.id)
    return {"config": cfg}


@admin_router.get("/api/admin/users/{user_id}/projects")
async def admin_user_projects(
    user_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Список ВСЕХ PlanFact-проектов целевого юзера + флаг is_active.

    Ключ берётся ТОЛЬКО из users.planfact_api_key (без env-fallback) —
    иначе админ увидит проекты под общим env-ключом, что путает.
    """
    from ..planfact import PlanFactClient, PlanFactError
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")

    pf_key = (u.planfact_api_key or "").strip()
    if not pf_key:
        return {
            "projects": [],
            "message": (
                "У пользователя не задан PlanFact API key. Сначала задайте "
                "через «Изменить» — потом сможете управлять доступом к проектам."
            ),
        }

    # Создаём отдельный клиент с ключом target-юзера. НЕ переиспользуем
    # планфактовский pool из app.planfact._clients (там ключ привязан к
    # current admin, и мы не хотим случайно загрязнить его кэш).
    pf = PlanFactClient(api_key=pf_key)
    try:
        projects = await pf.list_projects()
    except PlanFactError as e:
        raise HTTPException(502, f"PlanFact API: {e}")

    cfg = await store.list_projects_config(session, u.id)
    out = []
    for p in projects:
        pid = str(p.get("projectId") or p.get("id") or "")
        if not pid:
            continue
        c = cfg.get(pid) or {}
        out.append({
            "id": pid,
            "planfact_name": p.get("title") or p.get("name") or "",
            "is_active": bool(c.get("is_active", True)),
            "display_name": c.get("display_name"),
            "sort_order": c.get("sort_order"),
            "dodo_unit_uuid": c.get("dodo_unit_uuid"),
            "planfact_active": bool(p.get("active", True)),
        })
    return {"projects": out}


@admin_router.patch(
    "/api/admin/users/{user_id}/projects/{project_id}/config"
)
async def admin_patch_project_for_user(
    user_id: int,
    project_id: str,
    body: AdminProjectConfigUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Изменить is_active/display_name/sort_order/dodo_unit_uuid у конкретного
    проекта чужого юзера. None = не менять, '' = очистить (для текстовых)."""
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")

    kwargs: dict = {}
    if body.is_active is not None:
        kwargs["is_active"] = bool(body.is_active)
    if body.display_name is not None:
        kwargs["display_name"] = body.display_name
    if body.sort_order is not None:
        kwargs["sort_order"] = body.sort_order
    if body.dodo_unit_uuid is not None:
        kwargs["dodo_unit_uuid"] = body.dodo_unit_uuid

    await store.upsert_project_config(session, u.id, project_id, **kwargs)
    await session.commit()
    return {"status": "ok"}


# ---------- Audit log: глобальный для админа ----------

class AdminAuditEntry(BaseModel):
    id: int
    user_id: Optional[int]
    username: Optional[str]
    action: str
    details: Optional[dict]
    ip: Optional[str]
    user_agent: Optional[str]
    created_at: str


@admin_router.get("/api/admin/audit", response_model=list[AdminAuditEntry])
async def admin_list_audit(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    limit: int = 100,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
):
    """Audit-события всех юзеров. Опциональные фильтры: user_id, action."""
    from sqlalchemy import select
    from .models import AuditLog
    stmt = (
        select(AuditLog, User.username)
        .outerjoin(User, AuditLog.user_id == User.id)
        .order_by(AuditLog.created_at.desc())
        .limit(min(max(limit, 1), 500))
    )
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    result = await session.execute(stmt)
    return [
        AdminAuditEntry(
            id=row[0].id,
            user_id=row[0].user_id,
            username=row[1],
            action=row[0].action,
            details=row[0].details,
            ip=str(row[0].ip) if row[0].ip else None,
            user_agent=row[0].user_agent,
            created_at=row[0].created_at.isoformat(),
        )
        for row in result.all()
    ]
