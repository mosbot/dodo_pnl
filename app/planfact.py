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
    # LRU-bound: чтобы кэш не разрастался по памяти. operations за месяц могут
    # быть мегабайтами JSON.
    # Урезано 100→30 (S14-урок) — на XFood-ключе один месяц возвращает
    # 10k+ операций (parallel-split на под-кусочки), каждый ответ как Python
    # dict ~30-50 MB. 100 × 30 MB = 3 GB на одного юзера → за 6 мес Период
    # клали VM (6 GB RAM, swap=0). С 30 entries потолок ~1 GB.
    CACHE_MAX_ENTRIES = 30
    # /operations-ответы крупнее этого порога не кэшируем — single entry
    # размером в десятки мегабайт быстро заполнит LRU и вытолкнет всё
    # остальное. На больших ответах cost кэширования обычно перевешивает
    # выгоду (юзер редко повторяет тот же запрос со 100% совпадением фильтра).
    BIG_RESPONSE_SKIP_THRESHOLD = 5000  # items
    NO_CACHE_PATHS: tuple[str, ...] = ()

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        cache_ttl: int | None = None,
    ):
        self.api_key = api_key or settings.planfact_api_key
        self.base_url = (base_url or settings.planfact_base_url).rstrip("/")
        self.cache_ttl = cache_ttl if cache_ttl is not None else settings.cache_ttl
        # OrderedDict для LRU-эвикции (move_to_end на hit, popitem(last=False) на overflow)
        from collections import OrderedDict
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = asyncio.Lock()
        # Долгоживущий HTTP-клиент: один TLS handshake на инстанс, потом
        # keep-alive. Иначе каждый GET /operations делал бы новый
        # connect+TLS к api.planfact.ru — на «Период» (12 fetch'ей)
        # это +1-6 сек на ровном месте.
        # connect/read/write/pool — все 60s (как было). limits — щадящие
        # к PF API: max 10 keep-alive, max 20 одновременных коннектов.
        self._http: httpx.AsyncClient = httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )

    async def aclose(self) -> None:
        """Корректно закрыть HTTP-коннекты. Вызывается при invalidate."""
        try:
            await self._http.aclose()
        except Exception:
            pass

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
        # Большие endpoints (/operations) не кэшируем — каждый раз другие
        # фильтры, в кэше копится на десятки/сотни МБ за день.
        if any(np in path for np in self.NO_CACHE_PATHS):
            use_cache = False

        ck = self._cache_key(method, path, params, json)
        now = time.time()
        if use_cache and method == "GET":
            hit = self._cache.get(ck)
            if hit and now - hit[0] < self.cache_ttl:
                # LRU touch — двигаем в конец как недавно использованный
                self._cache.move_to_end(ck)
                return hit[1]
            elif hit:
                # Expired — удалить чтобы не накапливать
                self._cache.pop(ck, None)

        url = f"{self.base_url}{path}"
        r = await self._http.request(
            method, url, headers=self.headers, params=params, json=json,
        )
        if r.status_code >= 400:
            raise PlanFactError(r.status_code, r.text)
        raw = r.json() if r.content else {}

        data = _unwrap(raw)
        if use_cache and method == "GET":
            # Не кэшируем большие ответы (десятки MB на entry) — чтобы один
            # запрос не выжрал весь LRU. См. BIG_RESPONSE_SKIP_THRESHOLD.
            items_count = 0
            if isinstance(data, dict):
                its = data.get("items")
                if isinstance(its, list):
                    items_count = len(its)
            elif isinstance(data, list):
                items_count = len(data)
            if items_count <= self.BIG_RESPONSE_SKIP_THRESHOLD:
                self._cache[ck] = (now, data)
                # LRU eviction: вышли за бортик — выкидываем самый старый
                while len(self._cache) > self.CACHE_MAX_ENTRIES:
                    self._cache.popitem(last=False)
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

        use_cache=False: при раскрытии суммы юзеру нужны свежие данные, плюс
        фильтр (offset/limit/category_ids) меняется почти на каждый клик и
        кэш всё равно бесполезен.
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
            params["filter.projectId"] = project_ids
        if category_ids:
            params["filter.operationCategoryIds"] = category_ids
        data = await self._request(
            "GET", "/operations", params=params, use_cache=False,
        )
        if isinstance(data, dict):
            return data
        return {"items": data or [], "total": len(data or [])}

    # Сколько одновременных запросов в PF /operations — общий потолок,
    # включая parallel-split и parallel-by-project. PF не публикует rate-
    # limit, но эмпирически 8 параллельных вызовов проходят без 429.
    MAX_OPS_PARALLEL = 8

    async def fetch_all_operations(
        self,
        *,
        date_start: str,
        date_end: str,
        project_ids: list[str] | None = None,
        page_size: int = 10000,
        hard_limit: int = 50_000,
        method: str = "accrual",
    ) -> list[dict]:
        """Все операции за период — для сборки P&L.

        S11.1: PF API игнорирует offset/page — поэтому если ответ ≥ page_size,
        режем диапазон дат рекурсивно пополам и тянем половины.

        S11.2: оптимизация скорости двумя параллелизмами:
          1. Внутри split — left и right вызовы идут через asyncio.gather
             (раньше было последовательно).
          2. При нескольких project_ids — каждый проект тянется
             отдельным вызовом параллельно. Это резко уменьшает объём
             данных в каждом запросе (нет cross-project операций) и
             позволяет складывать ответы быстрее.

        Общий потолок на одновременные PF-запросы — semaphore(MAX_OPS_PARALLEL),
        чтобы не нарваться на rate-limit.
        """
        sem = asyncio.Semaphore(self.MAX_OPS_PARALLEL)

        if not project_ids or len(project_ids) <= 1:
            return await self._fetch_ops_recursive(
                sem=sem,
                date_start=date_start, date_end=date_end,
                project_ids=project_ids, method=method,
                page_size=page_size, hard_limit=hard_limit,
            )

        # Несколько проектов — параллелим по одному PF-вызову на проект.
        # PF возвращает операцию, если хотя бы одна часть привязана к
        # запрошенному проекту, значит cross-project операция придёт во
        # все запросы где она задействована — дедуплицируем по operationId.
        async def per_project(pid: str) -> list[dict]:
            return await self._fetch_ops_recursive(
                sem=sem,
                date_start=date_start, date_end=date_end,
                project_ids=[pid], method=method,
                page_size=page_size, hard_limit=hard_limit,
            )

        chunks = await asyncio.gather(*[per_project(p) for p in project_ids])

        seen: set[Any] = set()
        out: list[dict] = []
        for batch in chunks:
            for op in batch:
                oid = op.get("operationId")
                if oid in seen:
                    continue
                seen.add(oid)
                out.append(op)
                if len(out) >= hard_limit:
                    return out
        return out

    async def _fetch_ops_recursive(
        self,
        *,
        sem: asyncio.Semaphore,
        date_start: str,
        date_end: str,
        project_ids: list[str] | None,
        method: str,
        page_size: int,
        hard_limit: int,
    ) -> list[dict]:
        """Рекурсивно тянет операции за диапазон. Если PF отдал ровно
        page_size — режем пополам и тянем half'ы параллельно через gather.

        Возвращает список уникальных (по operationId) операций.
        """
        import logging
        log = logging.getLogger("uvicorn.error")

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
            params["filter.projectId"] = project_ids
        params["limit"] = page_size

        async with sem:
            data = await self._request("GET", "/operations", params=params)
        items = data.get("items") if isinstance(data, dict) else (data or [])
        if not items:
            return []

        # Если PF отдал < page_size — это полный набор за диапазон, можно
        # возвращать как есть (дедуп не нужен — внутри одного PF-ответа
        # operationId уникальны).
        if len(items) < page_size:
            return list(items)

        # Получили ровно лимит → возможно хвост обрезан. Делим пополам.
        if date_start < date_end:
            from datetime import date, timedelta
            try:
                d1 = date.fromisoformat(date_start)
                d2 = date.fromisoformat(date_end)
            except ValueError:
                log.warning("PF ops range %s..%s невалидный — split не делаем",
                            date_start, date_end)
                return list(items)
            mid = d1 + (d2 - d1) // 2
            mid_next = mid + timedelta(days=1)
            log.info(
                "PF /operations: %s≥page_size=%s, parallel-split %s..%s → %s..%s ‖ %s..%s",
                len(items), page_size, date_start, date_end,
                date_start, mid.isoformat(), mid_next.isoformat(), date_end,
            )
            left, right = await asyncio.gather(
                self._fetch_ops_recursive(
                    sem=sem,
                    date_start=date_start, date_end=mid.isoformat(),
                    project_ids=project_ids, method=method,
                    page_size=page_size, hard_limit=hard_limit,
                ),
                self._fetch_ops_recursive(
                    sem=sem,
                    date_start=mid_next.isoformat(), date_end=date_end,
                    project_ids=project_ids, method=method,
                    page_size=page_size, hard_limit=hard_limit,
                ),
            )
            # Дедуп union — на стыке суток одна операция может попасть в обе.
            seen: set[Any] = set()
            out: list[dict] = []
            for op in left:
                oid = op.get("operationId")
                if oid in seen:
                    continue
                seen.add(oid); out.append(op)
            for op in right:
                oid = op.get("operationId")
                if oid in seen:
                    continue
                seen.add(oid); out.append(op)
            return out

        # date_start == date_end и всё ещё ≥ page_size: один день, делить
        # уже некуда. Логируем warning, отдаём что есть.
        log.warning(
            "PF /operations: один день %s вернул %s операций — возможно "
            "усечение (PF лимит %s).",
            date_start, len(items), page_size,
        )
        return list(items)

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


# Per-user instance pool с LRU-эвикцией.
#
# Каждый PlanFactClient держит:
#   - долгоживущий httpx.AsyncClient (≈10 keep-alive коннектов)
#   - LRU-кэш до 30 ответов PF API (до ~30-50 МБ каждый для /operations)
#
# Без bound'а словарь рос без удержания — наблюдали OOM на VPS.
# Теперь храним не более CLIENTS_MAX живых инстансов, при переполнении
# выкидываем самый старый по last_used_at (popitem(last=False) на
# OrderedDict).
from collections import OrderedDict

CLIENTS_MAX = 50  # разумный лимит для VPS на ~30 пользователей

_clients: OrderedDict[int, PlanFactClient] = OrderedDict()


def _evict_lru_clients() -> None:
    """Удалить старейшие клиенты, пока размер не вернётся в пределы."""
    while len(_clients) > CLIENTS_MAX:
        _, victim = _clients.popitem(last=False)
        # Не ждём aclose — fire-and-forget, чтобы не блокировать caller.
        try:
            asyncio.get_event_loop().create_task(victim.aclose())
        except Exception:
            pass
        victim.invalidate_cache()


def get_planfact_client(user_id: int, api_key: str) -> PlanFactClient:
    """Получить (или создать) PlanFact-клиент для конкретного пользователя.

    Реюзаем инстанс, чтобы между запросами не терялся локальный TTL-cache
    и переиспользовались HTTP-коннекты. При hit двигаем в конец (LRU
    touch). При смене api_key у юзера старый инстанс выбрасываем
    (кэш построен под другой ключ).
    """
    existing = _clients.get(user_id)
    if existing is not None and existing.api_key == api_key:
        # LRU touch — этот юзер «свежий».
        _clients.move_to_end(user_id)
        return existing
    if existing is not None:
        # api_key сменился — закрываем старый клиент целиком.
        try:
            asyncio.get_event_loop().create_task(existing.aclose())
        except Exception:
            pass
        existing.invalidate_cache()
    new_client = PlanFactClient(api_key=api_key)
    _clients[user_id] = new_client
    _evict_lru_clients()
    return new_client


def invalidate_planfact_for(user_id: int) -> None:
    """Сбросить инстанс/кэш для юзера. Использовать при logout / смене ключа."""
    c = _clients.pop(user_id, None)
    if c is not None:
        try:
            asyncio.get_event_loop().create_task(c.aclose())
        except Exception:
            pass
        c.invalidate_cache()


# Глобальный singleton — оставлен только для совместимости со старым кодом,
# который ещё не перешёл на per-user. Удалить, когда все вызовы переедут.
client = PlanFactClient()
