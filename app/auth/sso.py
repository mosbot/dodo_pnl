"""SSO через sa: pnl потребляет сессию sa (общая кука .dodotool.ru).

Поток: браузер шлёт куку сессии sa и на pnl → pnl форвардит её в sa GET /me →
{sub, name}. По sub ищем pnl-юзера; если нет и у sa есть активная лицензия
(capability finance/pulse) — JIT-провижн: тенант (planfact_key) + network_admin
+ проекты из /entitlements. Затем вызывающий код создаёт обычную pnl-сессию.

PlanFact опционален: тенант создаётся с пустым api_key (Lite — данные из Dodo
IS). Локальный логин и ручное создание аккаунтов не затрагиваются.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from .models import PlanfactKey, User

log = logging.getLogger("uvicorn.error")

SA_COOKIE_NAME = "dt_session"       # кука сессии sa (переименована из default
                                    # "session", чтобы не конфликтовать со
                                    # старыми host-only куками при смене домена)
PNL_CAPABILITIES = ("finance", "pulse")  # что считаем «лицензией на pnl»


async def _sa_get(path: str, sa_cookie: str) -> Optional[dict]:
    if not settings.sa_base_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                settings.sa_base_url.rstrip("/") + path,
                cookies={SA_COOKIE_NAME: sa_cookie},
            )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001 — sa недоступен → SSO просто не сработает
        log.warning("SSO sa%s failed: %s", path, e)
        return None


async def resolve_sa_user(sa_cookie: str) -> Optional[dict]:
    """{sub, name} из sa /me или None (не аутентифицирован/sa недоступен)."""
    me = await _sa_get("/me", sa_cookie)
    if not me or not me.get("sub"):
        return None
    return {"sub": me["sub"], "name": (me.get("name") or "").strip()}


async def _licensed_units(sa_cookie: str) -> list[dict]:
    """Юниты с активной capability finance/pulse (= лицензия pnl)."""
    ent = await _sa_get("/entitlements", sa_cookie)
    out: list[dict] = []
    for u in (ent or {}).get("units", []):
        if set(u.get("capabilities") or []) & set(PNL_CAPABILITIES):
            out.append(u)
    return out


async def get_or_provision_user(
    db: AsyncSession, sa_cookie: str, sub: str, name: str,
) -> Optional[User]:
    """pnl-юзер по sub. Нет — JIT-провижн при наличии лицензии pnl. Иначе None."""
    u = (await db.execute(
        select(User).where(User.dodois_sub == sub)
    )).scalar_one_or_none()
    if u is not None:
        return u

    units = await _licensed_units(sa_cookie)
    if not units:
        log.info("SSO: sub=%s без лицензии pnl — доступ не выдаём", sub)
        return None

    from .. import dodois_client, store
    from .tokens import get_dodois_token

    tenant_name = (name or "Сеть") + f" ({sub[:6]})"
    pk = PlanfactKey(name=tenant_name, api_key="")  # PlanFact опционален (Lite)
    db.add(pk)
    await db.flush()  # pk.id

    admin = User(
        username=f"sso-{sub[:12]}",
        password_hash=None,
        display_name=name or tenant_name,
        dodois_sub=sub,
        role="network_admin",
        visibility_level=100,
        planfact_key_id=pk.id,
    )
    db.add(admin)
    await db.flush()

    # Имена пиццерий — через Dodo IS (токен по sub у брокера sa).
    uuid_name: dict[str, str] = {}
    try:
        token = await get_dodois_token(db, admin)
        for un in await dodois_client.fetch_units(token):
            uid = (un.get("id") or "").lower().replace("-", "")
            nm = un.get("name") or un.get("unitName")
            if uid and nm:
                uuid_name[uid] = nm
    except Exception as e:  # noqa: BLE001 — имена не критичны для провижна
        log.warning("SSO provision: имена юнитов не получены: %s", e)

    for un in units:
        uuid = un.get("dodois_uuid") or ""
        if not uuid:
            continue
        key = uuid.lower().replace("-", "")
        await store.upsert_project_config(
            db, pk.id, uuid,
            display_name=uuid_name.get(key) or uuid[:8],
            dodo_unit_uuid=uuid,
        )
    await db.commit()
    log.info("SSO provision: tenant=%s sub=%s units=%d", pk.id, sub, len(units))
    return admin
