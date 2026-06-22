"""Резолвер capability тенанта (лицензии) из sa.

Тенант (planfact_key) → его юниты (projects_config.dodo_unit_uuid) →
sa GET /internal/unit-capabilities (X-Admin-Token) → объединённый набор caps.
Кэш в памяти процесса (TTL). Fail-open: если sa не настроен/недоступен или у
тенанта нет юнитов — возвращаем None («неизвестно»), и гейт НЕ блокирует.
"""
from __future__ import annotations

import time

import httpx

from .config import settings
from . import store

_TTL_SEC = 300
_cache: dict[int, tuple[float, frozenset[str] | None]] = {}


def invalidate(planfact_key_id: int | None = None) -> None:
    """Сбросить кэш caps (для одного ключа или весь)."""
    if planfact_key_id is None:
        _cache.clear()
    else:
        _cache.pop(planfact_key_id, None)


async def get_tenant_capabilities(session, planfact_key_id: int | None) -> frozenset[str] | None:
    """Caps тенанта (объединение по его юнитам) или None («неизвестно» →
    гейт пропускает: fail-open). Кэш TTL=5 мин."""
    if not planfact_key_id:
        return None
    now = time.time()
    hit = _cache.get(planfact_key_id)
    if hit and (now - hit[0] < _TTL_SEC):
        return hit[1]
    caps = await _fetch(session, planfact_key_id)
    _cache[planfact_key_id] = (now, caps)
    return caps


async def _fetch(session, planfact_key_id: int) -> frozenset[str] | None:
    # sa не настроен → не enforce'им.
    if not settings.sa_token_broker_url or not settings.sa_internal_token:
        return None
    cfg = await store.list_projects_config(session, planfact_key_id)
    uuids = [
        c["dodo_unit_uuid"]
        for c in cfg.values()
        if c.get("is_active") and c.get("dodo_unit_uuid")
    ]
    if not uuids:
        return None  # нет юнитов — нечего проверять, не блокируем
    base = settings.sa_token_broker_url.rsplit("/", 1)[0]  # .../internal
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                base + "/unit-capabilities",
                params={"units": ",".join(uuids)},
                headers={"X-Admin-Token": settings.sa_internal_token},
            )
            resp.raise_for_status()
            return frozenset(resp.json().get("capabilities") or [])
    except Exception:
        # sa недоступен — fail-open (не лочим тенанта из-за сбоя инфры).
        return None
