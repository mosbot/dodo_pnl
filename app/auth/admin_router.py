"""Admin-эндпоинты: CRUD пользователей + cross-user управление проектами.

Все ручки гейтятся через Depends(require_admin) — non-admin получает 403.
Префикс /api/admin/* — отделён от собственных /api/me/*, чтобы было видно
из URL что это админ-операция.
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import store
from ..crypto import decrypt_secret, encrypt_secret
from ..db import get_session
from . import audit
from .dependencies import (
    require_admin, require_super_admin,
    require_admin_for_key, require_admin_for_user,
)
from .models import PlanfactKey, User
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
    role: str                          # 'super_admin' / 'network_admin' / 'user'
    is_admin: bool                     # legacy: True если super_admin or network_admin
    visibility_level: int
    dodois_credentials_name: Optional[str]
    planfact_key_id: Optional[int]
    planfact_key_name: Optional[str]   # для отображения вместо id
    created_at: str
    updated_at: str

    @classmethod
    def from_user(cls, u: User, key_name: Optional[str] = None) -> "AdminUserPublic":
        return cls(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            role=u.role,
            is_admin=u.is_any_admin,
            visibility_level=u.visibility_level,
            dodois_credentials_name=u.dodois_credentials_name,
            planfact_key_id=u.planfact_key_id,
            planfact_key_name=key_name,
            created_at=u.created_at.isoformat(),
            updated_at=u.updated_at.isoformat(),
        )


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    display_name: Optional[str] = None
    role: str = Field(default="user", pattern="^(super_admin|network_admin|user)$")
    visibility_level: int = Field(default=100, ge=0, le=100)
    dodois_credentials_name: Optional[str] = None
    planfact_key_id: Optional[int] = None


class AdminUserUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = Field(default=None, pattern="^(super_admin|network_admin|user)$")
    visibility_level: Optional[int] = Field(default=None, ge=0, le=100)
    dodois_credentials_name: Optional[str] = None
    planfact_key_id: Optional[int] = None
    # Явный флаг «сбросить привязку к PF-ключу» — нужен потому что None
    # в planfact_key_id означает «не менять» (как и в других полях).
    clear_planfact_key: bool = False


class ResetPasswordResponse(BaseModel):
    password: str
    detail: str = "Передайте этот пароль пользователю — повторно его узнать нельзя."


def _gen_password(n: int = 16) -> str:
    """Без спецсимволов в начале/конце, чтобы copy-paste не съел."""
    alpha = string.ascii_letters + string.digits
    return "".join(secrets.choice(alpha) for _ in range(n))


# ---------- CRUD users ----------

async def _key_names_map(session: AsyncSession) -> dict[int, str]:
    """id → name для всех записей planfact_keys. Один SELECT, чтобы не делать
    N+1 запросов при отрисовке списка юзеров."""
    from sqlalchemy import select
    res = await session.execute(select(PlanfactKey.id, PlanfactKey.name))
    return {row[0]: row[1] for row in res.all()}


@admin_router.get("/api/admin/users", response_model=list[AdminUserPublic])
async def admin_list_users(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    users = await list_users(session)
    # network_admin видит только юзеров своего PF-ключа.
    if admin.is_network_admin:
        users = [u for u in users if u.planfact_key_id == admin.planfact_key_id]
    keys = await _key_names_map(session)
    return [
        AdminUserPublic.from_user(u, key_name=keys.get(u.planfact_key_id))
        for u in users
    ]


@admin_router.post("/api/admin/users", response_model=AdminUserPublic)
async def admin_create_user(
    body: AdminUserCreate,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    # Network admin не может создать super_admin (privilege escalation).
    if body.role == "super_admin" and not admin.is_super_admin:
        raise HTTPException(403, "Только super-админ может создавать super-админов")
    # Network admin форсит planfact_key_id = self.planfact_key_id (создавать
    # юзеров может только в своей сети). Игнорируем body.planfact_key_id.
    if admin.is_network_admin:
        if body.planfact_key_id is not None and body.planfact_key_id != admin.planfact_key_id:
            raise HTTPException(403, "Можно создавать юзеров только в своей сети")
        body.planfact_key_id = admin.planfact_key_id
    try:
        u = await create_user(
            session,
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            role=body.role,
            visibility_level=body.visibility_level,
            dodois_credentials_name=body.dodois_credentials_name,
            planfact_key_id=body.planfact_key_id,
        )
        await audit.log_audit(
            session, audit.ACTION_ADMIN_USER_CREATED,
            user_id=admin.id, request=request,
            details={"target_user_id": u.id, "target_username": u.username, "role": u.role},
        )
        await session.commit()
        keys = await _key_names_map(session)
        return AdminUserPublic.from_user(u, key_name=keys.get(u.planfact_key_id))
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
    admin: User = Depends(require_admin_for_user("user_id")),
    session: AsyncSession = Depends(get_session),
):
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")

    # Защита от само-разжалования (пользователь не должен снять с себя
    # role!=super_admin). Также network_admin не может присвоить super_admin
    # роль никому (privilege escalation).
    if body.role is not None:
        if admin.id == u.id and body.role != "super_admin":
            raise HTTPException(400, "Нельзя снять super-admin с самого себя")
        if body.role == "super_admin" and not admin.is_super_admin:
            raise HTTPException(403, "Только super-админ может назначать super-админов")
    # Network admin не может перевести юзера в другую сеть.
    if admin.is_network_admin:
        if body.planfact_key_id is not None and body.planfact_key_id != admin.planfact_key_id:
            raise HTTPException(403, "Нельзя переназначить юзера в другую сеть")
        if body.clear_planfact_key:
            raise HTTPException(403, "Нельзя отвязать юзера от своей сети")

    fields_changed: list[str] = []
    if body.display_name is not None:
        u.display_name = body.display_name or None
        fields_changed.append("display_name")
    if body.role is not None:
        u.role = body.role
        fields_changed.append("role")
    if body.visibility_level is not None:
        u.visibility_level = int(body.visibility_level)
        fields_changed.append("visibility_level")
    integrations_changed = (
        body.dodois_credentials_name is not None
        or body.planfact_key_id is not None
        or body.clear_planfact_key
    )
    if integrations_changed:
        await update_integrations(
            session, u.id,
            dodois_credentials_name=body.dodois_credentials_name,
            planfact_key_id=body.planfact_key_id,
            clear_planfact_key=body.clear_planfact_key,
        )
        if body.dodois_credentials_name is not None:
            fields_changed.append("dodois_credentials_name")
        if body.planfact_key_id is not None or body.clear_planfact_key:
            fields_changed.append("planfact_key_id")

    await audit.log_audit(
        session, audit.ACTION_ADMIN_USER_UPDATED,
        user_id=admin.id, request=request,
        details={"target_user_id": u.id, "target_username": u.username,
                 "fields_changed": fields_changed},
    )
    await session.flush()
    await session.commit()
    fresh = await get_user_by_id(session, user_id)
    keys = await _key_names_map(session)
    return AdminUserPublic.from_user(fresh, key_name=keys.get(fresh.planfact_key_id))


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
    admin: User = Depends(require_admin_for_user("user_id")),
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
    is_admin_managed: Optional[bool] = None  # super_admin only
    display_name: Optional[str] = None
    sort_order: Optional[int] = None
    dodo_unit_uuid: Optional[str] = None


@admin_router.get("/api/admin/users/{user_id}/projects-config")
async def admin_user_projects_config(
    user_id: int,
    admin: User = Depends(require_admin_for_user("user_id")),
    session: AsyncSession = Depends(get_session),
):
    """Текущая конфигурация проектов под ключом этого юзера.
    Конфиг — общий на planfact_key, не per-user."""
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")
    if not u.planfact_key_id:
        return {"config": {}}
    cfg = await store.list_projects_config(session, u.planfact_key_id)
    return {"config": cfg}


# Удалено в S8.6: GET /api/admin/users/{user_id}/projects и
# PATCH /api/admin/users/{user_id}/projects/{project_id}/config —
# заменены на /api/admin/users/{user_id}/visibility и
# /api/admin/planfact-keys/{key_id}/projects.

# legacy stub (тело удалено) — оставлен только сигнатура чтобы старый
# фронт не падал с 404; эндпоинт сразу возвращает пустой список с
# подсказкой переустановить страницу:
@admin_router.get("/api/admin/users/{user_id}/projects")
async def admin_user_projects_deprecated(
    user_id: int,
    admin: User = Depends(require_admin_for_user("user_id")),
    session: AsyncSession = Depends(get_session),
):
    return {
        "projects": [],
        "message": (
            "Эндпоинт устарел. Обновите страницу: "
            "теперь модалка использует /visibility (per-user) "
            "и /planfact-keys/{id}/projects (структура)."
        ),
    }


# legacy fragment ниже сохранён для совместимости истории; будет вычищен
# в следующем рефакторинге, не вызывается фронтендом.
async def _legacy_admin_user_projects(
    user_id: int,
    admin: User,
    session: AsyncSession,
):
    """Список ВСЕХ PlanFact-проектов целевого юзера + флаг is_active.

    Ключ берётся ТОЛЬКО из users.planfact_api_key (без env-fallback) —
    иначе админ увидит проекты под общим env-ключом, что путает.
    """
    from ..planfact import PlanFactClient, PlanFactError
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")

    pf_key = ""
    if u.planfact_key_id:
        pk = await session.get(PlanfactKey, u.planfact_key_id)
        if pk:
            pf_key = decrypt_secret((pk.api_key or "").strip())
    if not pf_key:
        return {
            "projects": [],
            "message": (
                "У пользователя не назначен PlanFact-ключ. Сначала привяжите "
                "ключ из каталога через «Изменить» — потом сможете управлять "
                "доступом к проектам."
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

    cfg = await store.list_projects_config(session, u.planfact_key_id)
    out = []
    for p in projects:
        pid = str(p.get("projectId") or p.get("id") or "")
        if not pid:
            continue
        c = cfg.get(pid) or {}
        # group инфо из PlanFact (как и в /api/projects на главной)
        pg = p.get("projectGroup") or {}
        out.append({
            "id": pid,
            "planfact_name": p.get("title") or p.get("name") or "",
            "is_active": bool(c.get("is_active", True)),
            "display_name": c.get("display_name"),
            "sort_order": c.get("sort_order"),
            "dodo_unit_uuid": c.get("dodo_unit_uuid"),
            "planfact_active": bool(p.get("active", True)),
            "project_group_id": pg.get("projectGroupId"),
            "project_group_title": pg.get("title"),
            "project_group_is_undistributed": bool(pg.get("isUndistributed", False)),
        })
    return {"projects": out}


# Удалён старый PATCH /api/admin/users/{user_id}/projects/{pid}/config:
# его роль заменили /api/admin/planfact-keys/{key_id}/projects/{pid}/config
# (для структуры) и /api/admin/users/{user_id}/visibility/{pid} (для per-user
# видимости).


# ---------- Cross-user Dodo IS units (для модалки «Проекты» в админке) ----------

@admin_router.get("/api/admin/users/{user_id}/dodois-units")
async def admin_user_dodois_units(
    user_id: int,
    admin: User = Depends(require_admin_for_user("user_id")),
    session: AsyncSession = Depends(get_session),
):
    """Список юнитов Dodo IS под токеном целевого юзера. Нужно админу для
    выбора dodo_unit_uuid в модалке «Проекты»: токены у разных юзеров разные,
    подмножество юнитов тоже разное."""
    from .. import dodois_client
    from ..dodois_client import DodoISError
    from .tokens import NoTokenError, get_dodois_token
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")
    try:
        token = await get_dodois_token(session, u)
    except NoTokenError as e:
        return {"units": [], "message": str(e)}
    try:
        units = await dodois_client.fetch_units(token)
    except DodoISError as e:
        raise HTTPException(502, f"Dodo IS: {e}")
    pizzerias = [u for u in units if u.get("unitType") == 1]
    return {"units": pizzerias}


# ---------- Visibility per user (упрощённая модалка «Проекты юзера») ----------

class VisibilityUpdate(BaseModel):
    is_visible: bool


@admin_router.get("/api/admin/users/{user_id}/visibility")
async def admin_user_visibility(
    user_id: int,
    admin: User = Depends(require_admin_for_user("user_id")),
    session: AsyncSession = Depends(get_session),
):
    """Список проектов под ключом юзера + персональная видимость.
    Структурные поля (имя, порядок, dodo_unit) сюда НЕ возвращаются —
    они общие на ключ и редактируются через каталог PlanFact-ключей."""
    from ..planfact import PlanFactClient, PlanFactError
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")
    if not u.planfact_key_id:
        return {
            "projects": [],
            "message": "У пользователя не назначен PlanFact-ключ.",
        }

    pk = await session.get(PlanfactKey, u.planfact_key_id)
    pf_key = decrypt_secret((pk.api_key or "").strip()) if pk else ""
    if not pf_key:
        return {"projects": [], "message": "PF-ключ пустой."}

    pf = PlanFactClient(api_key=pf_key)
    try:
        projects = await pf.list_projects()
    except PlanFactError as e:
        raise HTTPException(502, f"PlanFact API: {e}")

    visibility = await store.list_user_visibility(session, u.id)
    out = []
    for p in projects:
        pid = str(p.get("projectId") or p.get("id") or "")
        if not pid:
            continue
        pg = p.get("projectGroup") or {}
        # is_visible default True если записи нет
        is_visible = visibility.get(pid, True)
        out.append({
            "id": pid,
            "planfact_name": p.get("title") or p.get("name") or "",
            "is_visible": is_visible,
            "planfact_active": bool(p.get("active", True)),
            "project_group_id": pg.get("projectGroupId"),
            "project_group_title": pg.get("title"),
            "project_group_is_undistributed": bool(pg.get("isUndistributed", False)),
        })
    return {"projects": out}


@admin_router.patch(
    "/api/admin/users/{user_id}/visibility/{project_id}"
)
async def admin_set_user_visibility(
    user_id: int, project_id: str,
    body: VisibilityUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    u = await get_user_by_id(session, user_id)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")
    await store.set_user_visibility(session, u.id, project_id, body.is_visible)
    await session.commit()
    return {"status": "ok"}


# ---------- Структура проектов на уровне PlanFact-ключа ----------

@admin_router.get("/api/admin/planfact-keys/{key_id}/projects")
async def admin_key_projects(
    key_id: int,
    admin: User = Depends(require_admin_for_key("key_id")),
    session: AsyncSession = Depends(get_session),
):
    """Структура проектов под ключом PlanFact (общая для всех юзеров ключа):
    is_active (архивация), display_name, sort_order, dodo_unit_uuid."""
    from ..planfact import PlanFactClient, PlanFactError
    pk = await session.get(PlanfactKey, key_id)
    if pk is None:
        raise HTTPException(404, "Ключ не найден")
    pf_key = decrypt_secret((pk.api_key or "").strip())
    if not pf_key:
        return {"projects": [], "message": "PF-ключ пустой."}

    pf = PlanFactClient(api_key=pf_key)
    try:
        projects = await pf.list_projects()
    except PlanFactError as e:
        raise HTTPException(502, f"PlanFact API: {e}")

    cfg = await store.list_projects_config(session, key_id)
    out = []
    for p in projects:
        pid = str(p.get("projectId") or p.get("id") or "")
        if not pid:
            continue
        c = cfg.get(pid) or {}
        is_admin_managed = bool(c.get("is_admin_managed", True))
        # network_admin не видит проекты, выключенные super-админом из
        # whitelist'а сети. super_admin видит всё (включая управление флагом).
        if admin.is_network_admin and not is_admin_managed:
            continue
        pg = p.get("projectGroup") or {}
        out.append({
            "id": pid,
            "planfact_name": p.get("title") or p.get("name") or "",
            "is_active": bool(c.get("is_active", True)),
            "is_admin_managed": is_admin_managed,
            "display_name": c.get("display_name"),
            "sort_order": c.get("sort_order"),
            "dodo_unit_uuid": c.get("dodo_unit_uuid"),
            "planfact_active": bool(p.get("active", True)),
            "project_group_id": pg.get("projectGroupId"),
            "project_group_title": pg.get("title"),
            "project_group_is_undistributed": bool(pg.get("isUndistributed", False)),
        })
    return {"projects": out, "viewer_role": admin.role}


@admin_router.patch(
    "/api/admin/planfact-keys/{key_id}/projects/{project_id}/config"
)
async def admin_patch_key_project_config(
    key_id: int, project_id: str,
    body: AdminProjectConfigUpdate,
    admin: User = Depends(require_admin_for_key("key_id")),
    session: AsyncSession = Depends(get_session),
):
    pk = await session.get(PlanfactKey, key_id)
    if pk is None:
        raise HTTPException(404, "Ключ не найден")

    # Network admin не может менять is_admin_managed (whitelist уровня сети).
    if body.is_admin_managed is not None and not admin.is_super_admin:
        raise HTTPException(
            403, "Только super-админ может менять «Доступ для сети»",
        )
    # Network admin: проверим что проект в whitelist'е (нельзя менять
    # is_active или другие поля у проекта который супер-админ скрыл).
    if admin.is_network_admin:
        cfg = await store.list_projects_config(session, key_id)
        c = cfg.get(project_id) or {}
        if not c.get("is_admin_managed", True):
            raise HTTPException(403, "Этот проект скрыт супер-админом сети")

    kwargs: dict = {}
    if body.is_active is not None:
        kwargs["is_active"] = bool(body.is_active)
    if body.is_admin_managed is not None:
        kwargs["is_admin_managed"] = bool(body.is_admin_managed)
    if body.display_name is not None:
        kwargs["display_name"] = body.display_name
    if body.sort_order is not None:
        kwargs["sort_order"] = body.sort_order
    if body.dodo_unit_uuid is not None:
        kwargs["dodo_unit_uuid"] = body.dodo_unit_uuid

    await store.upsert_project_config(session, key_id, project_id, **kwargs)
    await session.commit()
    return {"status": "ok"}


@admin_router.get("/api/admin/planfact-keys/{key_id}/dodois-units")
async def admin_key_dodois_units(
    key_id: int,
    admin: User = Depends(require_admin_for_key("key_id")),
    session: AsyncSession = Depends(get_session),
):
    """Список Dodo IS юнитов под токеном первого юзера, у которого есть
    привязанные dodois-кредсы. Используется в модалке «Проекты ключа» для
    подсказок при выборе dodo_unit_uuid."""
    from .. import dodois_client
    from ..dodois_client import DodoISError
    from .tokens import NoTokenError, get_dodois_token
    from sqlalchemy import select as _sel
    pk = await session.get(PlanfactKey, key_id)
    if pk is None:
        raise HTTPException(404, "Ключ не найден")

    # Первый юзер с этим ключом + непустым dodois_credentials_name
    stmt = (
        _sel(User)
        .where(
            User.planfact_key_id == key_id,
            User.dodois_credentials_name.isnot(None),
        )
        .order_by(User.id)
        .limit(1)
    )
    u = (await session.execute(stmt)).scalar_one_or_none()
    if u is None:
        return {"units": [], "message": "Ни у одного юзера ключа нет Dodo IS-кредсов."}
    try:
        token = await get_dodois_token(session, u)
    except NoTokenError as e:
        return {"units": [], "message": str(e)}
    try:
        units = await dodois_client.fetch_units(token)
    except DodoISError as e:
        raise HTTPException(502, f"Dodo IS: {e}")
    pizzerias = [u for u in units if u.get("unitType") == 1]
    return {"units": pizzerias}


# ---------- Каталог PlanFact-ключей ----------

class PlanfactKeyPublic(BaseModel):
    id: int
    name: str
    api_key_masked: str
    note: Optional[str]
    used_by_count: int   # сколько юзеров привязано
    live_months_window: int  # глубина live-окна для cache_history (S3.4)
    created_at: str
    updated_at: str


class PlanfactKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    api_key: str = Field(min_length=1)
    note: Optional[str] = None


class PlanfactKeyUpdate(BaseModel):
    name: Optional[str] = None
    api_key: Optional[str] = None     # None = не менять; пустая = ошибка
    note: Optional[str] = None


def _mask_key(s: str) -> str:
    """Маска для отображения api_key в админке. Принимает как зашифрованное
    значение (enc:...), так и legacy plain — расшифровывает через crypto."""
    s = decrypt_secret((s or "").strip())
    return (s[:4] + "..." + s[-4:]) if len(s) > 12 else "***"


async def _key_usage_count(session: AsyncSession, key_id: int) -> int:
    from sqlalchemy import func, select
    res = await session.execute(
        select(func.count(User.id)).where(User.planfact_key_id == key_id)
    )
    return int(res.scalar_one() or 0)


@admin_router.get("/api/admin/planfact-keys", response_model=list[PlanfactKeyPublic])
async def admin_list_planfact_keys(
    admin: User = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    """Каталог PF-ключей. Только super_admin — network видит только свой
    ключ через /api/admin/planfact-keys/{key_id}/* эндпойнты."""
    from sqlalchemy import select
    res = await session.execute(
        select(PlanfactKey).order_by(PlanfactKey.name)
    )
    items = list(res.scalars())
    out: list[PlanfactKeyPublic] = []
    for k in items:
        cnt = await _key_usage_count(session, k.id)
        out.append(PlanfactKeyPublic(
            id=k.id, name=k.name, api_key_masked=_mask_key(k.api_key),
            note=k.note, used_by_count=cnt,
            live_months_window=k.live_months_window,
            created_at=k.created_at.isoformat(),
            updated_at=k.updated_at.isoformat(),
        ))
    return out


@admin_router.post("/api/admin/planfact-keys", response_model=PlanfactKeyPublic)
async def admin_create_planfact_key(
    body: PlanfactKeyCreate,
    admin: User = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    pk = PlanfactKey(
        name=body.name.strip(),
        api_key=encrypt_secret(body.api_key.strip()),
        note=(body.note or "").strip() or None,
    )
    session.add(pk)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, f"Ключ с именем {body.name!r} уже существует")
    return PlanfactKeyPublic(
        id=pk.id, name=pk.name, api_key_masked=_mask_key(pk.api_key),
        note=pk.note, used_by_count=0,
        live_months_window=pk.live_months_window,
        created_at=pk.created_at.isoformat(),
        updated_at=pk.updated_at.isoformat(),
    )


@admin_router.patch("/api/admin/planfact-keys/{key_id}", response_model=PlanfactKeyPublic)
async def admin_update_planfact_key(
    key_id: int,
    body: PlanfactKeyUpdate,
    admin: User = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    pk = await session.get(PlanfactKey, key_id)
    if pk is None:
        raise HTTPException(404, "Ключ не найден")
    if body.name is not None:
        pk.name = body.name.strip()
    if body.api_key is not None:
        if not body.api_key.strip():
            raise HTTPException(400, "api_key не может быть пустым")
        pk.api_key = encrypt_secret(body.api_key.strip())
    if body.note is not None:
        pk.note = body.note.strip() or None
    pk.updated_at = datetime.now(timezone.utc)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Ключ с таким именем уже существует")
    cnt = await _key_usage_count(session, pk.id)
    return PlanfactKeyPublic(
        id=pk.id, name=pk.name, api_key_masked=_mask_key(pk.api_key),
        note=pk.note, used_by_count=cnt,
        live_months_window=pk.live_months_window,
        created_at=pk.created_at.isoformat(),
        updated_at=pk.updated_at.isoformat(),
    )


@admin_router.delete("/api/admin/planfact-keys/{key_id}")
async def admin_delete_planfact_key(
    key_id: int,
    admin: User = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    pk = await session.get(PlanfactKey, key_id)
    if pk is None:
        raise HTTPException(404, "Ключ не найден")
    cnt = await _key_usage_count(session, pk.id)
    if cnt > 0:
        raise HTTPException(
            409,
            f"Этот ключ используется {cnt} пользователем(ями). Сначала отвяжите.",
        )
    await session.delete(pk)
    await session.commit()
    return {"status": "ok"}


# ---------- Cache history (S3.5) ----------
# Снэпшоты P&L по закрытым месяцам. Лежат на уровне planfact_key.

class CacheEntryPublic(BaseModel):
    kind: str
    period_month: str
    frozen_at: Optional[str]
    frozen_by_user_id: Optional[int]
    frozen_by_username: Optional[str] = None


class LiveMonthsWindowUpdate(BaseModel):
    live_months_window: int = Field(ge=1, le=24)


@admin_router.get(
    "/api/admin/planfact-keys/{key_id}/cache",
    response_model=list[CacheEntryPublic],
)
async def admin_list_key_cache(
    key_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Список замороженных месяцев под ключом + кто и когда заморозил."""
    pk = await session.get(PlanfactKey, key_id)
    if pk is None:
        raise HTTPException(404, "Ключ не найден")
    entries = await store.list_cache_entries(session, key_id)
    if not entries:
        return []
    # Resolve username для frozen_by_user_id (одним SELECT, не N+1).
    user_ids = sorted({e["frozen_by_user_id"] for e in entries if e["frozen_by_user_id"]})
    usernames: dict[int, str] = {}
    if user_ids:
        res = await session.execute(
            select(User.id, User.username).where(User.id.in_(user_ids))
        )
        usernames = {row[0]: row[1] for row in res.all()}
    return [
        CacheEntryPublic(
            kind=e["kind"],
            period_month=e["period_month"],
            frozen_at=e["frozen_at"],
            frozen_by_user_id=e["frozen_by_user_id"],
            frozen_by_username=usernames.get(e["frozen_by_user_id"]),
        )
        for e in entries
    ]


@admin_router.delete(
    "/api/admin/planfact-keys/{key_id}/cache/{period_month}"
)
async def admin_delete_key_cache(
    key_id: int, period_month: str,
    kind: str = "planfact_pnl",
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Переоткрытие месяца. Следующий запрос за этот период пересоберёт
    снэпшот из live PlanFact-данных. Используется когда бухгалтер
    задним числом изменил операции в PF и нужно подтянуть свежие цифры."""
    pk = await session.get(PlanfactKey, key_id)
    if pk is None:
        raise HTTPException(404, "Ключ не найден")
    await store.delete_cache_entry(session, key_id, period_month, kind=kind)
    await session.commit()
    return {"status": "ok", "period_month": period_month}


@admin_router.patch(
    "/api/admin/planfact-keys/{key_id}/live-months-window"
)
async def admin_update_live_months_window(
    key_id: int,
    body: LiveMonthsWindowUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Глубина live-окна — сколько последних месяцев читаются live из PF
    (всегда). Старые читаются из cache_history. Дефолт = 2 (текущий + 1
    предыдущий). Поднять до 4 если бухгалтер закрывает квартал."""
    pk = await session.get(PlanfactKey, key_id)
    if pk is None:
        raise HTTPException(404, "Ключ не найден")
    pk.live_months_window = int(body.live_months_window)
    pk.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"status": "ok", "live_months_window": pk.live_months_window}


# ---------- Список Dodo IS логинов из соседской БД ----------

class DodoisCredential(BaseModel):
    name: str
    email: Optional[str]


@admin_router.get("/api/admin/dodois-credentials", response_model=list[DodoisCredential])
async def admin_list_dodois_credentials(
    admin: User = Depends(require_super_admin),
    session: AsyncSession = Depends(get_session),
):
    """Список доступных Dodo IS логинов из public.dodois_credentials.
    Только колонки name + email — токены не отдаём наружу."""
    from sqlalchemy import text
    res = await session.execute(
        text("SELECT name, email FROM public.dodois_credentials ORDER BY name")
    )
    return [DodoisCredential(name=row[0], email=row[1]) for row in res.all()]


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
    admin: User = Depends(require_super_admin),
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
