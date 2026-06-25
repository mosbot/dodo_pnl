"""Запросы доступа: резолв тенанта по юнитам + CRUD AccessRequest.

SSO-флоу: незнакомый Dodo IS-юзер, чья сеть уже заведена тенантом, запрашивает
доступ; сетевой админ одобряет. `dodois_sub` всегда берётся из валидной
sa-сессии (не из формы).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ProjectConfig
from .models import AccessRequest


def _norm(u: str | None) -> str:
    return (u or "").lower().replace("-", "")


async def find_tenant_by_units(
    session: AsyncSession, unit_uuids: list[str]
) -> Optional[int]:
    """planfact_key с наибольшим пересечением АКТИВНЫХ заведений с набором.

    «К какой сети относятся эти заведения». None — ни один тенант не владеет
    переданными юнитами (сеть ещё не онбордилась)."""
    wanted = {_norm(u) for u in unit_uuids if u}
    if not wanted:
        return None
    rows = (await session.execute(
        select(ProjectConfig.planfact_key_id, ProjectConfig.dodo_unit_uuid).where(
            ProjectConfig.dodo_unit_uuid.isnot(None),
            ProjectConfig.is_active.is_(True),
        )
    )).all()
    counts: dict[int, int] = {}
    for pf, raw in rows:
        if _norm(raw) in wanted:
            counts[pf] = counts.get(pf, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


async def get_pending(
    session: AsyncSession, planfact_key_id: int, dodois_sub: str
) -> Optional[AccessRequest]:
    return (await session.execute(
        select(AccessRequest).where(
            AccessRequest.planfact_key_id == planfact_key_id,
            AccessRequest.dodois_sub == dodois_sub,
            AccessRequest.status == "pending",
        )
    )).scalar_one_or_none()


async def create_request(
    session: AsyncSession, *, planfact_key_id: int, dodois_sub: str,
    name: Optional[str], email: Optional[str], units: Optional[list],
) -> AccessRequest:
    """Создать pending-запрос (идемпотентно: существующий pending — возвращаем)."""
    existing = await get_pending(session, planfact_key_id, dodois_sub)
    if existing is not None:
        return existing
    req = AccessRequest(
        planfact_key_id=planfact_key_id, dodois_sub=dodois_sub,
        name=name, email=email, units=units, status="pending",
    )
    session.add(req)
    await session.flush()
    return req


async def list_pending_for_key(
    session: AsyncSession, planfact_key_id: int
) -> list[AccessRequest]:
    return list((await session.execute(
        select(AccessRequest)
        .where(
            AccessRequest.planfact_key_id == planfact_key_id,
            AccessRequest.status == "pending",
        )
        .order_by(AccessRequest.created_at)
    )).scalars())


async def get_request(
    session: AsyncSession, req_id: int
) -> Optional[AccessRequest]:
    return await session.get(AccessRequest, req_id)


async def mark_decided(
    session: AsyncSession, req: AccessRequest, *, status: str, decided_by: int
) -> None:
    req.status = status
    req.decided_by = decided_by
    req.decided_at = datetime.now(timezone.utc)
    await session.flush()
