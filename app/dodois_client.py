"""Dodo IS API client.

На данном этапе токен читаем из settings.dodo_is_access_token (env
DODO_IS_ACCESS_TOKEN). В проде заменится на чтение из Postgres соседнего
сервиса, у которого уже настроен OAuth refresh flow.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx

from .config import settings


log = logging.getLogger(__name__)

# Большие окна (полный месяц × несколько юнитов) Dodo IS отдаёт небыстро —
# по 30–60 сек на запрос. Делаем таймаут с запасом.
_REQ_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

# Параллелизм per-unit запросов. До 6 в одном батче — у типичной франшизы
# 5-7 пиццерий, бьёмся в одну волну. Если Dodo IS начнёт рвать коннекты
# (ReadError, RemoteProtocolError) — _with_retries поймает и повторит.
_MAX_PARALLEL = 6

# Сетевые ошибки, на которые имеет смысл ретраить: transport-level, без ответа
# от сервера. 4xx/5xx прилетают как DodoISError из _raise — их не ретраим,
# чтобы не маскировать auth-проблемы.
_TRANSIENT_EXC = (
    httpx.ReadError,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.WriteError,
)


class DodoISError(Exception):
    """Обёртка над ошибками Dodo IS API."""


async def _with_retries(
    op_name: str,
    fn: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 4,
    base_delay: float = 1.5,
) -> Any:
    """Выполнить async-операцию с retry на транзиентные сетевые ошибки.

    Экспоненциальный backoff с джиттером: ~1.5s, ~3s, ~6s. Не ретраит
    DodoISError (HTTP 4xx/5xx) — это не сетевой сбой и обычно означает
    проблему с токеном/правами.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except _TRANSIENT_EXC as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, 0.5)
            log.warning(
                "Dodo IS %s: transient %s (attempt %d/%d), retry in %.1fs",
                op_name, type(exc).__name__, attempt, attempts, delay,
            )
            await asyncio.sleep(delay)
    raise DodoISError(
        f"Dodo IS {op_name}: сетевая ошибка после {attempts} попыток: "
        f"{type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def _headers(token: str) -> dict[str, str]:
    """Bearer-заголовок. Token приходит из app.auth.tokens.get_dodois_token()
    в вызывающей точке (main.py), не из settings. Это даёт per-user
    разделение токенов и позволяет соседскому сервису обновлять access_token
    в БД без перезапуска нашего сервиса."""
    if not token:
        raise DodoISError("Empty Dodo IS token passed to client")
    return {"Authorization": f"Bearer {token}"}


def _raise(response: httpx.Response) -> None:
    if response.is_success:
        return
    # Dodo IS отдаёт JSON-ошибки в двух форматах; нам важна понятность
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:400]}
    raise DodoISError(
        f"Dodo IS API {response.status_code} {response.request.url.path}: {body}"
    )


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


async def fetch_units(token: str) -> list[dict[str, Any]]:
    """GET /auth/roles/units — список всех юнитов пользователя."""
    url = f"{settings.dodo_is_auth_url}/roles/units"
    async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as http:
        r = await http.get(url, headers=_headers(token))
        _raise(r)
        data = r.json()
    return data if isinstance(data, list) else []


async def fetch_productivity(
    token: str, unit_uuid: str, from_date: datetime, to_date: datetime
) -> dict[str, Any] | None:
    """GET /production/productivity — по одному юниту."""
    url = f"{settings.dodo_is_base_url}/production/productivity"
    params = {"from": _fmt(from_date), "to": _fmt(to_date), "units": unit_uuid}
    async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as http:
        r = await http.get(url, headers=_headers(token), params=params)
        _raise(r)
        data = r.json()
    items = data.get("productivityStatistics") or []
    return items[0] if items else None


async def _fetch_productivity_one(
    http: httpx.AsyncClient, sem: asyncio.Semaphore, token: str, unit_uuid: str,
    from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    url = f"{settings.dodo_is_base_url}/production/productivity"
    params = {"from": _fmt(from_date), "to": _fmt(to_date), "units": unit_uuid}

    async def _do() -> list[dict[str, Any]]:
        async with sem:
            r = await http.get(url, headers=_headers(token), params=params)
        _raise(r)
        return r.json().get("productivityStatistics") or []

    return await _with_retries(f"productivity[{unit_uuid[:8]}]", _do)


async def fetch_productivity_many(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> list[dict[str, Any]]:
    """Параллельно по юнитам с retry и concurrency-limit."""
    if not unit_uuids:
        return []
    sem = asyncio.Semaphore(_MAX_PARALLEL)
    async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as http:
        results = await asyncio.gather(
            *[_fetch_productivity_one(http, sem, token, u, from_date, to_date) for u in unit_uuids]
        )
    out: list[dict[str, Any]] = []
    for r in results:
        out.extend(r)
    return out


async def _fetch_delivery_stats_one(
    http: httpx.AsyncClient, sem: asyncio.Semaphore, token: str, unit_uuid: str,
    from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    url = f"{settings.dodo_is_base_url}/delivery/statistics"
    params = {"from": _fmt(from_date), "to": _fmt(to_date), "units": unit_uuid}

    async def _do() -> list[dict[str, Any]]:
        async with sem:
            r = await http.get(url, headers=_headers(token), params=params)
        _raise(r)
        return r.json().get("unitsStatistics") or []

    return await _with_retries(f"delivery-stats[{unit_uuid[:8]}]", _do)


async def fetch_delivery_statistics(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> list[dict[str, Any]]:
    if not unit_uuids:
        return []
    sem = asyncio.Semaphore(_MAX_PARALLEL)
    async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as http:
        results = await asyncio.gather(
            *[_fetch_delivery_stats_one(http, sem, token, u, from_date, to_date) for u in unit_uuids]
        )
    out: list[dict[str, Any]] = []
    for r in results:
        out.extend(r)
    return out


async def _fetch_vouchers_one(
    http: httpx.AsyncClient, sem: asyncio.Semaphore, token: str, unit_uuid: str,
    from_date: datetime, to_date: datetime,
) -> list[dict[str, Any]]:
    url = f"{settings.dodo_is_base_url}/delivery/vouchers"
    take = 1000
    skip = 0
    out: list[dict[str, Any]] = []
    while True:
        params = {
            "from": _fmt(from_date), "to": _fmt(to_date),
            "units": unit_uuid, "take": take, "skip": skip,
        }

        async def _do() -> dict[str, Any]:
            async with sem:
                r = await http.get(url, headers=_headers(token), params=params)
            _raise(r)
            return r.json()

        data = await _with_retries(f"vouchers[{unit_uuid[:8]}@{skip}]", _do)
        page = data.get("vouchers") or []
        out.extend(page)
        if data.get("isEndOfListReached") or len(page) < take:
            break
        skip += take
    return out


async def fetch_late_delivery_vouchers(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> list[dict[str, Any]]:
    if not unit_uuids:
        return []
    sem = asyncio.Semaphore(_MAX_PARALLEL)
    async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as http:
        results = await asyncio.gather(
            *[_fetch_vouchers_one(http, sem, token, u, from_date, to_date) for u in unit_uuids]
        )
    out: list[dict[str, Any]] = []
    for r in results:
        out.extend(r)
    return out


async def fetch_late_delivery_vouchers_count(
    token: str, unit_uuids: list[str], from_date: datetime, to_date: datetime
) -> dict[str, int]:
    items = await fetch_late_delivery_vouchers(token, unit_uuids, from_date, to_date)
    counts: dict[str, int] = {}
    for it in items:
        uid = (it.get("unitId") or "").lower().replace("-", "")
        if not uid:
            continue
        counts[uid] = counts.get(uid, 0) + 1
    return counts
