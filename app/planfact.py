"""Клиент PlanFact API с in-memory кэшем.

Все ответы PlanFact обёрнуты в envelope:
    {"data": <payload>, "isSuccess": bool, "errorMessage": str|None, ...}
Пагинированные эндпоинты: payload = {"items": [...], "total": N, "deletedItems": [...], "totalDeleted": N}.
У /bizinfos/* payload это просто список.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .config import settings


class PlanFactError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"PlanFact API {status}: {body[:400]}")


def _unwrap(payload: Any) -> Any:
    """Снять конверт {data, isSuccess, errorMessage}."""
    if isinstance(payload, dict) and "isSuccess" in payload and "data" in payload:
        if not payload.get("isSuccess", True):
            raise PlanFactError(200, payload.get("errorMessage") or "PlanFact returned isSuccess=false")
        return payload["data"]
    return payload


class PlanFactClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        cache_ttl: int | None = None,
    ):
        self.api_key = api_key or settings.planfact_api_key
        self.base_url = (base_url or settings.planfact_base_url).rstrip("/")
        self.cache_ttl = cache_ttl if cache_ttl is not None else settings.cache_ttl
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-ApiKey": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _cache_key(self, method: str, path: str, params: dict | None, body: Any) -> str:
        return f"{method}|{path}|{sorted((params or {}).items())}|{body!r}"

    def invalidate_cache(self) -> None:
        self._cache.clear()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        use_cache: bool = True,
    ) -> Any:
        ck = self._cache_key(method, path, params, json)
        now = time.time()
        if use_cache and method == "GET":
            hit = self._cache.get(ck)
            if hit and now - hit[0] < self.cache_ttl:
                return hit[1]

        async with httpx.AsyncClient(timeout=60.0) as http:
            url = f"{self.base_url}{path}"
            r = await http.request(method, url, headers=self.headers, params=params, json=json)
            if r.status_code >= 400:
                raise PlanFactError(r.status_code, r.text)
            raw = r.json() if r.content else {}

        data = _unwrap(raw)
        if use_cache and method == "GET":
            self._cache[ck] = (now, data)
        return data

    # --- high-level methods ---

    async def list_projects(self) -> list[dict]:
        return await self._fetch_all_pages("/projects")

    async def list_operation_categories(self) -> list[dict]:
        return await self._fetch_all_pages("/operationcategories")

    async def list_operations(
        self,
        *,
        date_start: str,
        date_end: str,
        project_ids: list[str] | None = None,
        category_ids: list[str] | None = None,
        offset: int = 0,
        limit: int = 100,
        method: str = "accrual",
    ) -> dict:
        """Одна страница операций (для drill-down в UI).

        method="accrual" → фильтр по дате начисления (filter.calculationPeriodDateStart/End);
        method="cash"    → по дате движения денег (filter.operationDateStart/End).
        """
        if method == "accrual":
            params: dict[str, Any] = {
                "filter.calculationPeriodDateStart": date_start,
                "filter.calculationPeriodDateEnd": date_end,
            }
        else:
            params = {
                "filter.operationDateStart": date_start,
                "filter.operationDateEnd": date_end,
            }
        params["offset"] = offset
        params["limit"] = limit
        if project_ids:
            params["filter.projectIds"] = project_ids
        if category_ids:
            params["filter.operationCategoryIds"] = category_ids
        data = await self._request("GET", "/operations", params=params)
        if isinstance(data, dict):
            return data
        return {"items": data or [], "total": len(data or [])}

    async def fetch_all_operations(
        self,
        *,
        date_start: str,
        date_end: str,
        project_ids: list[str] | None = None,
        page_size: int = 10000,
        hard_limit: int = 200_000,
        method: str = "accrual",
    ) -> list[dict]:
        """Все операции за период — для сборки P&L.

        По умолчанию — метод начисления: фильтр filter.calculationPeriodDateStart/End.
        Возвращаем всё, что сервер согласился отдать под этот период;
        на клиенте потом будем фильтровать по operationPart.calculationDate,
        чтобы отнести суммы строго к нужному месяцу.
        """
        if method == "accrual":
            params: dict[str, Any] = {
                "filter.calculationPeriodDateStart": date_start,
                "filter.calculationPeriodDateEnd": date_end,
            }
        else:
            params = {
                "filter.operationDateStart": date_start,
                "filter.operationDateEnd": date_end,
            }
        if project_ids:
            params["filter.projectIds"] = project_ids

        all_items: list[dict] = []
        offset = 0
        while True:
            page_params = dict(params, offset=offset, limit=page_size)
            data = await self._request("GET", "/operations", params=page_params)
            items = data.get("items") if isinstance(data, dict) else (data or [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < page_size:
                break
            offset += page_size
            if offset >= hard_limit:
                break
        return all_items

    async def _fetch_all_pages(self, path: str, page_size: int = 1000) -> list[dict]:
        """Перебор страниц для /projects, /operationcategories и т.п."""
        results: list[dict] = []
        offset = 0
        while True:
            page = await self._request(
                "GET", path,
                params={"offset": offset, "limit": page_size},
            )
            if isinstance(page, dict):
                items = page.get("items") or page.get("data") or []
            elif isinstance(page, list):
                items = page
            else:
                items = []
            if not items:
                break
            results.extend(items)
            if len(items) < page_size:
                break
            offset += page_size
            if offset > 50_000:
                break
        return results


# Per-user instance pool. Сохраняем кэш PlanFact-ответов между запросами
# одного пользователя (внутри инстанса есть TTL-cache по cache_key).
# При смене API-ключа у юзера старый инстанс протухает — пересоздаём.
_clients: dict[int, PlanFactClient] = {}


def get_planfact_client(user_id: int, api_key: str) -> PlanFactClient:
    """Получить (или создать) PlanFact-клиент для конкретного пользователя.

    Реюзаем инстанс, чтобы между запросами не терялся локальный TTL-cache.
    Если api_key поменялся — выбрасываем старый инстанс целиком (включая
    кэш — он построен под другой ключ и может содержать чужие проекты).
    """
    existing = _clients.get(user_id)
    if existing is not None and existing.api_key == api_key:
        return existing
    new_client = PlanFactClient(api_key=api_key)
    _clients[user_id] = new_client
    return new_client


def invalidate_planfact_for(user_id: int) -> None:
    """Сбросить инстанс/кэш для юзера. Использовать при logout / смене ключа."""
    c = _clients.pop(user_id, None)
    if c is not None:
        c.invalidate_cache()


# Глобальный singleton — оставлен только для совместимости со старым кодом,
# который ещё не перешёл на per-user. Удалить, когда все вызовы переедут.
client = PlanFactClient()
