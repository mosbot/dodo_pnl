"""FastAPI-приложение: дашборд + API.

Multi-tenant: каждый запрос несёт user (из require_user dependency) и session
(из get_session). Все обращения к данным идут через app.store.* с
обязательным owner_id=user.id.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from . import board as board_module
from . import dodois_client
from . import pnl as pnl_module
from . import store
from .auth.admin_router import admin_router
from .auth.dependencies import (
    optional_user, require_admin, require_territorial, require_user,
)
from .auth.router import router as auth_router
from . import formulas, schemas
from .auth.models import User
from .auth.tokens import NoTokenError, get_dodois_token, get_planfact_key
from .config import settings
from .db import get_session
from .dodois_client import DodoISError
from .planfact import PlanFactClient, PlanFactError, get_planfact_client, invalidate_planfact_for
from .planfact_export import ExportParseError, parse_pnl_export
from .schemas import (
    DefaultTargetIn,
    OpsMetricIn,
    OpsProjectTargetIn,
    OpsTargetIn,
    ProjectConfigIn,
    SettingIn,
    TargetIn,
    TemplateNodeCodeIn,
    TemplateSaveIn,
)


app = FastAPI(title="PnL Dashboard")


@app.on_event("startup")
async def _require_secret_key() -> None:
    """V9 (code-review 2026-06-10): без SECRET_KEY шифрование PlanFact-ключей
    в crypto.py тихо деградирует до plaintext. На проде (Postgres задан) это
    недопустимо — отказываемся стартовать. Локальная разработка без
    DATABASE_URL продолжает работать как раньше."""
    if settings.database_url and not settings.secret_key:
        raise RuntimeError(
            "SECRET_KEY не задан при настроенном DATABASE_URL — секреты "
            "писались бы в БД открытым текстом. Сгенерируй: "
            "python -c 'import secrets; print(secrets.token_hex(32))' "
            "и положи в .env как SECRET_KEY=..."
        )


@app.on_event("shutdown")
async def _close_dodois_shared_client() -> None:
    """Закрыть общий httpx-клиент Dodo IS (см. dodois_client._client)."""
    from . import dodois_client
    await dodois_client.aclose_shared_client()


# --- security headers ---
# CSP: только self — Chart.js self-host в static/vendor/ (V7, code-review
# 2026-06-10: весь jsdelivr в script-src = готовый CSP-bypass гаджет).
# inline-стили пока разрешены (некоторые модалки используют style=...).
_SEC_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


@app.middleware("http")
async def security_headers(request, call_next):
    """Добавляем security-заголовки ко всем ответам."""
    resp = await call_next(request)
    for k, v in _SEC_HEADERS.items():
        # Не перезаписываем, если кто-то выше уже выставил (например,
        # /static может иметь свой CSP).
        if k not in resp.headers:
            resp.headers[k] = v
    return resp


# --- auth ---
# Подключаем /auth/login, /auth/logout, /auth/me + /api/me/* + /api/admin/*
app.include_router(auth_router)
app.include_router(admin_router)


# --- token resolver helpers ---

async def planfact_for(
    session: AsyncSession, user: User
) -> PlanFactClient:
    """Достать актуальный PlanFact-клиент для текущего пользователя.

    Per-user instance кэшируется в planfact._clients — переиспользуем
    локальный TTL-cache между запросами одного юзера.
    """
    api_key = await get_planfact_key(session, user)
    return get_planfact_client(user.id, api_key)


async def with_dodois_retry(session: AsyncSession, user: User, fn, *args, **kwargs):
    """Вызвать dodois-функцию с обработкой 401: один retry с force-reload токена.

    Сценарий: соседский cron только что обновил access_token в
    public.dodois_credentials. Наш свежий запрос пошёл со старым токеном
    из памяти — получили 401. Перечитываем токен из БД и повторяем — должно
    пройти. Если повторно 401 — это уже настоящий auth-сбой, поднимаем
    наверх как 502.
    """
    import logging
    log = logging.getLogger(__name__)
    token = await get_dodois_token(session, user)
    try:
        return await fn(token, *args, **kwargs)
    except DodoISError as e:
        msg = str(e)
        # _raise() в dodois_client формирует строку с HTTP-статусом —
        # ищем " 401 " в детали (со пробелами, чтобы не зацепить случайное "401").
        if " 401 " not in msg and "401 " not in msg.split(":", 1)[0]:
            raise
        log.warning("Dodo IS 401 — pulling fresh token from DB and retrying")
        fresh_token = await get_dodois_token(session, user)
        # Если токен в БД совпал с прежним (cron ещё не сработал) — повтор
        # бесполезен, сразу поднимаем оригинальную ошибку, чтобы юзер увидел
        # реальную причину.
        if fresh_token == token:
            raise
        return await fn(fresh_token, *args, **kwargs)


@app.exception_handler(NoTokenError)
async def _no_token_handler(request: Request, exc: NoTokenError):
    """NoTokenError → 400 с детальным сообщением для UI."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _require_user_pf_key(user: User) -> int:
    """Достать planfact_key_id юзера или вернуть 400 если не настроен.

    Используется во всех админских ручках, которые пишут в таблицы,
    привязанные к ключу (template, metrics, targets и пр.).
    """
    if not user.planfact_key_id:
        raise HTTPException(
            400,
            "У пользователя не задан ключ PlanFact. "
            "Настройте его в /settings → Интеграции."
        )
    return user.planfact_key_id


# --- Авто-привязка PlanFact projects ↔ Dodo IS units по имени ---

def _normalize_name(s: str) -> str:
    """Нормализация для match'инга имён: lowercase + убрать пробелы/дефисы/нбсп."""
    import re
    s = (s or "").strip().lower().replace("\xa0", " ")
    return re.sub(r"[\s\-_]+", "", s)


@app.post("/api/projects/auto-link-dodois")
async def auto_link_dodois(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Автопривязка projects_config.dodo_unit_uuid по совпадающему имени.
    Проекты с уже непустым uuid пропускаем. Юниты, которые уже привязаны к
    другому проекту, не используем повторно."""
    pf = await planfact_for(session, user)
    try:
        projects = await pf.list_projects()
    except PlanFactError as e:
        raise HTTPException(502, f"PlanFact: {e}")

    try:
        token = await get_dodois_token(session, user)
        units = await dodois_client.fetch_units(token)
    except DodoISError as e:
        raise HTTPException(502, f"Dodo IS: {e}")

    pizzerias = [u for u in units if u.get("unitType") == 1]
    pf_key_id = _require_user_pf_key(user)
    cfg = await store.list_projects_config(session, pf_key_id)

    units_by_norm: dict[str, dict] = {}
    for u in pizzerias:
        n = _normalize_name(u.get("name") or "")
        if n:
            units_by_norm[n] = u

    used_uuids = {
        c["dodo_unit_uuid"] for c in cfg.values() if c.get("dodo_unit_uuid")
    }

    linked: list[dict] = []
    skipped_already: list[dict] = []
    no_match: list[dict] = []
    duplicate: list[dict] = []

    for p in projects:
        pid = str(p.get("projectId") or p.get("id") or "")
        if not pid:
            continue
        pf_name = p.get("title") or p.get("name") or ""
        existing = cfg.get(pid) or {}
        if existing.get("dodo_unit_uuid"):
            skipped_already.append({"project_id": pid, "name": pf_name})
            continue
        norm = _normalize_name(pf_name)
        match = units_by_norm.get(norm)
        if not match:
            no_match.append({"project_id": pid, "name": pf_name})
            continue
        uuid = match.get("id")
        if uuid in used_uuids:
            duplicate.append({
                "project_id": pid, "name": pf_name,
                "unit_name": match.get("name"), "unit_id": uuid,
            })
            continue
        await store.upsert_project_config(
            session, pf_key_id, pid, dodo_unit_uuid=uuid
        )
        used_uuids.add(uuid)
        linked.append({
            "project_id": pid, "name": pf_name,
            "unit_name": match.get("name"), "unit_id": uuid,
        })

    await session.commit()
    return {
        "linked": linked,
        "skipped_already_linked": skipped_already,
        "no_match": no_match,
        "duplicate_unit_id": duplicate,
        "summary": (
            f"Привязано: {len(linked)}, уже было: {len(skipped_already)}, "
            f"не нашли пару: {len(no_match)}, дубль uuid: {len(duplicate)}"
        ),
    }


# --- Test-connection эндпоинты для UI «Интеграции» ---

@app.post("/api/me/test-planfact")
async def test_planfact_connection(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
):
    """Проверить, что текущий PlanFact key работает. Делает GET /companies
    (минимальный валидный запрос). Возвращает {ok: bool, detail: str, ...}."""
    try:
        pf = await planfact_for(session, user)
    except NoTokenError as e:
        return {"ok": False, "detail": str(e)}
    try:
        # Самый дешёвый запрос — список проектов
        projects = await pf.list_projects()
        return {
            "ok": True,
            "detail": f"OK — найдено {len(projects)} проект(ов)",
            "projects_count": len(projects),
        }
    except PlanFactError as e:
        return {"ok": False, "detail": f"PlanFact API: {e}"}


@app.post("/api/me/test-dodois")
async def test_dodois_connection(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
):
    """Проверить Dodo IS access_token: GET /auth/roles/units."""
    try:
        token = await get_dodois_token(session, user)
    except NoTokenError as e:
        return {"ok": False, "detail": str(e)}
    try:
        units = await dodois_client.fetch_units(token)
        pizzerias = [u for u in units if u.get("unitType") == 1]
        return {
            "ok": True,
            "detail": f"OK — доступ к {len(pizzerias)} пиццериям",
            "units_count": len(pizzerias),
        }
    except DodoISError as e:
        return {"ok": False, "detail": f"Dodo IS: {e}"}


# --- static files ---
static_dir = Path(__file__).resolve().parent.parent / "static"


class SafeStaticFiles(StaticFiles):
    """StaticFiles с deny-листом по расширению.

    Background (2026-05-08 → 2026-06-03): в `static/` оказались копии backend
    исходников (main.py, pnl.py, models.py, schemas.py, storage.py). Они
    отдавались публично по https://pnl.dodotool.ru/static/main.py с
    HTTP 200. Дефолтный StaticFiles раздаёт ВСЁ что лежит в директории —
    нет встроенной фильтрации по типу файла.

    Defense-in-depth: даже если кто-то снова случайно скопирует исходники
    в static/ (или сложит туда .env, .key, .pem) — раздать не дадим.
    """

    DENIED_EXTENSIONS = frozenset({
        ".py", ".pyc", ".pyo", ".pyd",       # python source/bytecode
        ".env", ".envrc",                     # secrets
        ".key", ".pem", ".crt", ".p12",       # tls/crypto
        ".db", ".sqlite", ".sqlite3",         # data
        ".sh", ".bash",                       # shell scripts
        ".yml", ".yaml",                      # configs (compose/k8s/ansible)
        ".toml", ".ini", ".cfg",              # configs
        ".log",                                # logs
        ".sql",                                # db dumps
    })

    async def get_response(self, path, scope):
        from starlette.responses import Response
        # Любой компонент пути с deny-расширением → 404 (не 403, чтобы не
        # подтверждать существование файла).
        low = path.lower()
        for ext in self.DENIED_EXTENSIONS:
            if low.endswith(ext):
                return Response(status_code=404)
        return await super().get_response(path, scope)


app.mount("/static", SafeStaticFiles(directory=str(static_dir)), name="static")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Страница логина — публичная, без auth-зависимости."""
    return (static_dir / "login.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index(user: User | None = Depends(optional_user)):
    """Дашборд. Без сессии — редирект на /login (а не 401), чтобы пользователь
    видел форму логина, а не голый JSON."""
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return HTMLResponse((static_dir / "index.html").read_text(encoding="utf-8"))


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(user: User | None = Depends(optional_user)):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return HTMLResponse((static_dir / "settings.html").read_text(encoding="utf-8"))


@app.get("/board", response_class=HTMLResponse)
async def board_page(user: User | None = Depends(optional_user)):
    """Страница «День» — оперативная сводка по выбранным проектам.
    Доступна всем авторизованным юзерам. Управляющий видит свою точку,
    территориальный — свои, сетевой — все (по visibility + drawer-выбор)."""
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return HTMLResponse((static_dir / "board.html").read_text(encoding="utf-8"))


# --- API routes ---

@app.get("/api/health")
async def health(
    user: User | None = Depends(optional_user),
):
    """Live-проверка. Анонимам — только {"status": "ok"} (V10, code-review
    2026-06-10: RSS/threads/кол-во тенантов — информация для разведки).
    Админам — диагностика памяти (для отладки утечек без SSH): читает
    /proc/self/status (нативно в Linux, не требует psutil), возвращает RSS,
    кол-во инстансов PlanFactClient и общий размер их кэшей.
    """
    import os
    if user is None or not user.is_any_admin:
        return {"status": "ok"}
    info: dict = {"status": "ok"}
    try:
        with open(f"/proc/{os.getpid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    info["rss_kb"] = int(line.split()[1])
                elif line.startswith("VmSize:"):
                    info["vsize_kb"] = int(line.split()[1])
                elif line.startswith("Threads:"):
                    info["threads"] = int(line.split()[1])
    except Exception:
        pass

    # Размер per-user PlanFact-кэша
    from .planfact import _clients
    info["planfact_clients"] = len(_clients)
    info["planfact_cache_entries_total"] = sum(
        len(c._cache) for c in _clients.values()
    )
    return info


@app.get("/api/projects")
async def get_projects(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
):
    pf = await planfact_for(session, user)
    try:
        projects = await pf.list_projects()
    except PlanFactError as e:
        raise HTTPException(502, str(e))
    cfg = (
        await store.list_projects_config(session, user.planfact_key_id)
        if user.planfact_key_id else {}
    )
    # Личный фильтр видимости: то, что админ выключил конкретно этому юзеру.
    hidden = await store.get_user_hidden_projects(session, user.id)
    # super_admin видит ВСЕ проекты (включая выключенные whitelist'ом),
    # network_admin и user — только те где is_admin_managed=True.
    # Это первый слой видимости: «доступен ли проект сети вообще».
    show_admin_unmanaged = user.is_super_admin
    norm = []
    for p in projects:
        pid = str(p.get("projectId") or p.get("id") or "")
        if not pid:
            continue
        c = cfg.get(pid) or {}
        is_admin_managed = bool(c.get("is_admin_managed", True))
        if not is_admin_managed and not show_admin_unmanaged:
            continue
        # is_active = «архивация на ключ» AND «не скрыт лично у юзера».
        # Юзер увидит проект на главной только когда оба true.
        key_active = bool(c.get("is_active", True))
        user_visible = pid not in hidden
        # Группа из PlanFact: projectGroup={projectGroupId, title, isUndistributed, active}.
        pg = p.get("projectGroup") or {}
        norm.append({
            "id": pid,
            "planfact_name": p.get("title") or p.get("name") or "",
            "name": c.get("display_name") or p.get("title") or p.get("name") or "",
            "display_name": c.get("display_name"),
            "is_active": key_active and user_visible,
            "key_active": key_active,
            "user_visible": user_visible,
            "sort_order": c.get("sort_order"),
            "planfact_active": bool(p.get("active", True)),
            "dodo_unit_uuid": c.get("dodo_unit_uuid"),
            "project_group_id": pg.get("projectGroupId"),
            "project_group_title": pg.get("title"),
            "project_group_is_undistributed": bool(pg.get("isUndistributed", False)),
        })
    return {"projects": norm}


def _derive_period_month(date_start: str, date_end: str) -> str | None:
    if not date_start or not date_end or len(date_start) < 7 or len(date_end) < 7:
        return None
    if date_start[:7] == date_end[:7]:
        return date_start[:7]
    return None


def _is_full_month(date_start: str, date_end: str, period_month: str | None) -> bool:
    """True если запрос охватывает РОВНО один календарный месяц (1-е → конец).

    Кэшируем только полно-месячные запросы. Партиальные диапазоны пропускаем
    в live, иначе будем сохранять «кусок месяца» как закрытый месяц.
    """
    if not period_month or len(period_month) != 7:
        return False
    try:
        y, m = map(int, period_month.split("-"))
    except ValueError:
        return False
    from calendar import monthrange
    last = monthrange(y, m)[1]
    return (
        date_start == f"{period_month}-01"
        and date_end == f"{period_month}-{last:02d}"
    )


def _current_month_str() -> str:
    """Текущий месяц 'YYYY-MM' в системной таймзоне сервера."""
    from datetime import date
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


async def _build_revenue_history_window(
    *,
    session: AsyncSession,
    user: User,
    pf: PlanFactClient,
    period_months: list[str],
    project_filter: list[str] | None,
    method: str,
) -> dict:
    """Собрать историю выручки для окна `period_months`, используя
    cache_history для замороженных месяцев — операции из PlanFact качаем
    только за live-окно. См. S9.3.

    Возвращает в формате build_revenue_history (months/totals/by_channel/
    projects/project_names).
    """
    from calendar import monthrange
    from collections import defaultdict
    from . import pnl as pnl_module

    pf_key_id = user.planfact_key_id

    # Глубина live-окна берётся с PF-ключа (S3.4). Без ключа — кэшем
    # пользоваться нельзя, всё качается live.
    if pf_key_id:
        from .auth.models import PlanfactKey
        pk = await session.get(PlanfactKey, pf_key_id)
        lmw = pk.live_months_window if pk else 2
    else:
        lmw = 0

    cur_month = _current_month_str()

    # Делим месяцы окна на «cache hit» и «нужно качать live».
    cached: dict[str, dict] = {}
    live_set: set[str] = set()
    for m in period_months:
        if (
            pf_key_id
            and lmw > 0
            and not store.is_period_in_live_window(m, cur_month, lmw)
        ):
            payload = await store.get_cache_entry(session, pf_key_id, m)
            if payload:
                cached[m] = payload
                continue
        live_set.add(m)

    # Заготовки результата
    totals = {m: 0.0 for m in period_months}
    by_channel = {
        m: {ch: 0.0 for ch in pnl_module.REVENUE_CHANNELS}
        for m in period_months
    }
    by_project: dict[str, dict[str, float]] = defaultdict(
        lambda: {m: 0.0 for m in period_months}
    )
    project_names: dict[str, str] = {}

    # Заполняем из cache_history
    proj_filter_set = set(project_filter) if project_filter else None
    for m, payload in cached.items():
        rbc = payload.get("revenue_by_channel") or {}
        for pid, channels in rbc.items():
            if proj_filter_set is not None and pid not in proj_filter_set:
                continue
            for ch in pnl_module.REVENUE_CHANNELS:
                v = float((channels or {}).get(ch, 0.0))
                if v == 0:
                    continue
                totals[m] += v
                by_channel[m][ch] += v
                by_project[pid][m] += v

    # Live-месяцы тянем одним PF-запросом (минимальный диапазон).
    if live_set:
        live_months = sorted(live_set)
        date_start = f"{live_months[0]}-01"
        last_y, last_m = (int(x) for x in live_months[-1].split("-"))
        date_end = f"{live_months[-1]}-{monthrange(last_y, last_m)[1]:02d}"
        _, categories, operations = await _fetch_period(
            pf, date_start, date_end, project_filter, method=method,
        )
        live_hist = await pnl_module.build_revenue_history(
            session=session, owner_id=user.id, planfact_key_id=pf_key_id,
            categories=categories, operations=operations,
            project_filter=project_filter,
            months=live_months, method=method,
        )
        for m in live_months:
            totals[m] = live_hist["totals"].get(m, 0.0)
            month_ch = live_hist["by_channel"].get(m) or {}
            for ch in pnl_module.REVENUE_CHANNELS:
                by_channel[m][ch] = float(month_ch.get(ch, 0.0))
            for pid, monthly in (live_hist.get("projects") or {}).items():
                by_project[pid][m] = monthly.get(m, 0.0)
        project_names.update(live_hist.get("project_names") or {})

    # Если не ходили в PF (всё из кэша) — для имён проектов всё-таки
    # дёргаем pf.list_projects() (один лёгкий запрос с TTL).
    missing = set(by_project.keys()) - set(project_names.keys())
    if missing:
        try:
            projects = await pf.list_projects()
            for p in projects:
                pid = str(p.get("projectId") or p.get("id") or "")
                if pid in missing:
                    project_names[pid] = p.get("title") or p.get("name") or ""
        except PlanFactError:
            pass  # имена опциональны — фронт показывает projectId

    return {
        "months": period_months,
        "totals": totals,
        "by_channel": by_channel,
        "projects": dict(by_project),
        "project_names": project_names,
    }


async def _build_pnl_for_period(
    *,
    session: AsyncSession,
    user: User,
    pf: PlanFactClient,
    date_start: str,
    date_end: str,
    period_month: str | None,
    project_filter: list[str] | None,
    method: str,
) -> dict:
    """Получить P&L за период с учётом кэша закрытых месяцев (S3.5).

    Логика:
      1) Период вне live-окна И полно-месячный И ключ задан → пытаемся читать
         cache_history. Hit → передаём в build_pnl как cached_aggregates.
      2) Cache miss в той же ситуации → fetch ВСЕХ операций ключа (без
         project_filter), build_pnl(cache_mode="save"), сохраняем агрегаты.
         project_filter применяется только при выборе shown_project_ids,
         поэтому юзер сразу видит свой срез, а кэш — общий на ключ.
      3) Иначе — старая live-логика.
    """
    pf_key_id = user.planfact_key_id

    # S20: источник P&L-агрегата ('raw' | 'shadow' | 'v2') — флаг на ключе.
    pnl_source = "raw"
    pk = None
    if pf_key_id is not None:
        from .auth.models import PlanfactKey
        pk = await session.get(PlanfactKey, pf_key_id)
        pnl_source = (getattr(pk, "pnl_source", None) or "raw")

    cacheable = (
        pf_key_id is not None
        and period_month is not None
        and _is_full_month(date_start, date_end, period_month)
    )

    cached_payload: dict | None = None
    use_cache_after_miss = False
    if cacheable:
        lmw = pk.live_months_window if pk else 2
        if not store.is_period_in_live_window(
            period_month, _current_month_str(), lmw,
        ):
            cached_payload = await store.get_cache_entry(
                session, pf_key_id, period_month,
            )
            use_cache_after_miss = cached_payload is None

    if cached_payload is not None:
        # HIT: операции не нужны, тянем только projects+categories
        # (они описывают структуру и в любом случае читаются live).
        import asyncio
        projects, categories = await asyncio.gather(
            pf.list_projects(),
            pf.list_operation_categories(),
        )
        return await pnl_module.build_pnl(
            session=session, owner_id=user.id,
            planfact_key_id=pf_key_id,
            categories=categories, operations=[], projects=projects,
            project_filter=project_filter,
            date_start=date_start, date_end=date_end,
            method=method, period_month=period_month,
            user_visibility_level=user.visibility_level,
            cached_aggregates=cached_payload,
        )

    if use_cache_after_miss:
        # MISS: ключ-уровневый снэпшот (без project_filter), агрегируем
        # для всего ключа, рендерим под фильтр юзера, сохраняем в cache_history.
        result = None
        if pnl_source == "v2":
            # S20: снэпшот из v2 (агрегат и так ключе-уровневый).
            result = await _build_pnl_v2_result(
                session=session, user=user, pf=pf,
                date_start=date_start, date_end=date_end,
                period_month=period_month, project_filter=project_filter,
                method=method, cache_mode="save",
            )
        if result is None:
            projects, categories, operations = await _fetch_period(
                pf, date_start, date_end, None, method=method,
            )
            result = await pnl_module.build_pnl(
                session=session, owner_id=user.id,
                planfact_key_id=pf_key_id,
                categories=categories, operations=operations, projects=projects,
                project_filter=project_filter,
                date_start=date_start, date_end=date_end,
                method=method, period_month=period_month,
                user_visibility_level=user.visibility_level,
                cache_mode="save",
            )
        agg = result.pop("_cache_aggregates", None)
        if agg is not None:
            await store.save_cache_entry(
                session, pf_key_id, period_month, agg,
                frozen_by_user_id=user.id,
            )
            await session.commit()
        return result

    # Live (текущий месяц или multi-month).
    # S20: источник v2 (один лёгкий POST вместо 30-50 МБ операций) с
    # автоматическим fallback на raw при любой проблеме.
    if pnl_source == "v2":
        v2_result = await _build_pnl_v2_result(
            session=session, user=user, pf=pf,
            date_start=date_start, date_end=date_end,
            period_month=period_month, project_filter=project_filter,
            method=method,
        )
        if v2_result is not None:
            return v2_result
        # fallback: предупреждение уже в логе, продолжаем raw-путём.

    projects, categories, operations = await _fetch_period(
        pf, date_start, date_end, project_filter, method=method,
    )
    raw_result = await pnl_module.build_pnl(
        session=session, owner_id=user.id,
        planfact_key_id=pf_key_id,
        categories=categories, operations=operations, projects=projects,
        project_filter=project_filter,
        date_start=date_start, date_end=date_end,
        method=method, period_month=period_month,
        user_visibility_level=user.visibility_level,
    )

    # S20 shadow: ответ отдаём из raw, а в фоне сверяем с v2 и логируем
    # дельты построчно. Throttle, чтобы «Период» (12 месяцев = 13 вызовов)
    # не плодил задачи.
    if pnl_source == "shadow":
        _schedule_v2_shadow(
            user_id=user.id, pf_key_id=pf_key_id,
            date_start=date_start, date_end=date_end,
            period_month=period_month, project_filter=project_filter,
            method=method,
            raw_line_totals={
                str(ln.get("code")): float((ln.get("total") or {}).get("amount") or 0)
                for ln in (raw_result.get("lines") or [])
            },
        )
    return raw_result


# S22: маппинг каналов Dodo IS → наши revenue_channel.
_DODO_CHANNEL_MAP = {"Delivery": "delivery", "Dine-in": "restaurant", "Takeaway": "takeaway"}


async def _maybe_override_revenue_from_dodois(
    *,
    session: AsyncSession,
    user: User,
    aggregates: dict,
    cat_index: dict,
    date_start: str,
    date_end: str,
    period_month: str | None,
    cache_mode: str,
) -> dict:
    """S22: для live ТЕКУЩЕГО полного месяца заменить REVENUE и разбивку по
    каналам на данные Dodo IS (/finances/sales/units/monthly).

    No-op (возвращает aggregates без изменений), если:
      - не live-режим (cache_mode != 'off' → закрытый месяц, immutable PF);
      - период не равен текущему полному календарному месяцу;
      - флаг ключа live_revenue_from_dodois выключен;
      - у ключа нет привязанных Dodo-юнитов или категорий выручки;
      - Dodo IS недоступен / любая ошибка.
    НИКОГДА не бросает — выручка PlanFact остаётся страховкой.

    ВАЖНО про слой инъекции: отображаемая строка REVENUE считается
    _apply_metric_formulas из шаблона ПланФакт, который строится из
    CAT_TOTALS (а не из totals[(pid,'REVENUE')]). Поэтому подменяем именно
    cat_totals категорий выручки: канал Dodo → категория с тем же
    revenue_channel; «прочие» revenue-категории зануляем (туда попадает
    артефакт «Нераспределенный доход»). totals и revenue_by_channel
    выставляем согласованно, чтобы знаменатели % и канальные пиллы совпали.
    """
    import logging
    from collections import defaultdict
    log = logging.getLogger("uvicorn.error")

    if cache_mode != "off":
        return aggregates
    if not (period_month and period_month == _current_month_str()
            and _is_full_month(date_start, date_end, period_month)):
        return aggregates
    pf_key_id = user.planfact_key_id
    if pf_key_id is None:
        return aggregates

    try:
        from .auth.models import PlanfactKey
        pk = await session.get(PlanfactKey, pf_key_id)
        if not getattr(pk, "live_revenue_from_dodois", False):
            return aggregates
        cfg = await store.list_projects_config(session, pf_key_id)
        uuid_to_pid: dict[str, str] = {}
        uuids: list[str] = []
        for pid, c in cfg.items():
            uu = c.get("dodo_unit_uuid")
            if c.get("is_active") and uu:
                uuid_to_pid[board_module._normalize_uuid(uu)] = pid
                uuids.append(uu)
        if not uuids:
            return aggregates
        token = await get_dodois_token(session, user)
        rows = await dodois_client.fetch_finance_sales_monthly(
            token, uuids, date_start, date_end,
        )
    except Exception:
        log.exception(
            "S22 dodo-revenue %s: fetch failed — оставляем выручку PlanFact",
            period_month,
        )
        return aggregates

    # Агрегируем продажи Dodo и каналы по project_id.
    dodo_ch: dict[str, dict[str, float]] = {}
    for r in rows:
        pid = uuid_to_pid.get(board_module._normalize_uuid(r.get("unitId", "")))
        if not pid:
            continue
        chd = dodo_ch.setdefault(
            pid, {ch: 0.0 for ch in pnl_module.REVENUE_CHANNELS},
        )
        for b in (r.get("salesBreakdown") or []):
            ch = _DODO_CHANNEL_MAP.get(b.get("salesChannel"), "other")
            chd[ch] += float(b.get("sales") or 0)
    if not dodo_ch:
        return aggregates

    # REVENUE-категории шаблона, сгруппированные по каналу (из cat_index).
    rev_cids_by_channel: dict[str, list[str]] = defaultdict(list)
    all_rev_cids: list[str] = []
    for cid, info in cat_index.items():
        if (info or {}).get("pnl_code") == "REVENUE":
            ch = info.get("revenue_channel") or "other"
            rev_cids_by_channel[ch].append(str(cid))
            all_rev_cids.append(str(cid))
    if not all_rev_cids:
        log.warning(
            "S22 dodo-revenue %s: у ключа %s нет REVENUE-категорий — "
            "подмена невозможна, оставляем PlanFact", period_month, pf_key_id,
        )
        return aggregates
    # Детерминированный приоритет cid внутри канала: cid с наибольшей текущей
    # суммой по ключу (стабильный «основной» счёт канала).
    cat_totals = aggregates.setdefault("cat_totals", {})
    _key_cid_weight: dict[str, float] = defaultdict(float)
    for k, v in cat_totals.items():
        try:
            _, cid = k.split("|", 1)
        except ValueError:
            continue
        _key_cid_weight[cid] += abs(float(v or 0))
    for ch in rev_cids_by_channel:
        rev_cids_by_channel[ch].sort(key=lambda c: -_key_cid_weight.get(c, 0.0))

    def _primary_cid(ch: str) -> str | None:
        """cid для размещения суммы канала: своя категория канала → иначе
        delivery → restaurant → любой revenue cid (чтобы итог не потерялся)."""
        for cand in (ch, "delivery", "restaurant", "takeaway", "other"):
            if rev_cids_by_channel.get(cand):
                return rev_cids_by_channel[cand][0]
        return all_rev_cids[0] if all_rev_cids else None

    totals = aggregates.setdefault("totals", {})
    rbc = aggregates.setdefault("revenue_by_channel", {})
    apids = set(aggregates.get("active_project_ids") or [])

    for pid, chans in dodo_ch.items():
        # 1) обнуляем ВСЕ revenue-категории точки (в т.ч. артефакт «прочих»).
        for cid in all_rev_cids:
            kk = f"{pid}|{cid}"
            if kk in cat_totals:
                cat_totals[kk] = 0.0
        # 2) раскладываем суммы каналов Dodo по соответствующим категориям.
        for ch, amt in chans.items():
            if not amt:
                continue
            target = _primary_cid(ch)
            if target is None:
                continue
            kk = f"{pid}|{target}"
            cat_totals[kk] = cat_totals.get(kk, 0.0) + amt
        # 3) согласованные totals (знаменатель %) и каналы (пиллы).
        totals[f"{pid}|REVENUE"] = sum(chans.values())
        rbc[pid] = {c: chans.get(c, 0.0) for c in pnl_module.REVENUE_CHANNELS}
        apids.add(pid)

    aggregates["active_project_ids"] = sorted(apids)
    log.info(
        "S22 dodo-revenue %s: переопределено %d точек, total=%.0f",
        period_month, len(dodo_ch),
        sum(sum(c.values()) for c in dodo_ch.values()),
    )
    return aggregates


async def _build_pnl_v2_result(
    *,
    session: AsyncSession,
    user: User,
    pf: PlanFactClient,
    date_start: str,
    date_end: str,
    period_month: str | None,
    project_filter: list[str] | None,
    method: str,
    cache_mode: str = "off",
) -> dict | None:
    """S20: собрать P&L из POST /api/v2/reports/opu.

    Возвращает результат build_pnl или None — сигнал caller'у уйти на
    raw-fallback (любая ошибка v2/адаптера логируется, но НЕ роняет запрос:
    raw-путь полнофункционален).
    """
    import logging
    from . import pnl_v2
    log = logging.getLogger("uvicorn.error")
    try:
        projects, categories = await asyncio.gather(
            pf.list_projects(),
            pf.list_operation_categories(),
        )
        report = await pf.report_opu(
            date_start=date_start, date_end=date_end, method=method,
        )
        cat_index = await pnl_module._build_category_index(
            session, user.id, user.planfact_key_id, categories,
        )
        aggregates = pnl_v2.v2_to_aggregates(report, cat_index)
    except pnl_v2.V2AdapterError as e:
        log.warning("pnl-v2 %s..%s: %s — fallback на raw", date_start, date_end, e)
        return None
    except PlanFactError as e:
        log.warning("pnl-v2 %s..%s: PF error %s — fallback на raw",
                    date_start, date_end, e)
        return None
    except Exception:
        # Любая неожиданность не должна ронять P&L — raw нас страхует.
        log.exception("pnl-v2 %s..%s: unhandled — fallback на raw",
                      date_start, date_end)
        return None

    # S22: для live текущего месяца подменяем REVENUE на свежие продажи Dodo IS
    # (PlanFact подтягивает день к ~23:15). No-op для закрытых месяцев и при
    # выключенном флаге ключа; сбой Dodo не ломает ответ.
    aggregates = await _maybe_override_revenue_from_dodois(
        session=session, user=user, aggregates=aggregates, cat_index=cat_index,
        date_start=date_start, date_end=date_end,
        period_month=period_month, cache_mode=cache_mode,
    )

    result = await pnl_module.build_pnl(
        session=session, owner_id=user.id,
        planfact_key_id=user.planfact_key_id,
        categories=categories, operations=[], projects=projects,
        project_filter=project_filter,
        date_start=date_start, date_end=date_end,
        method=method, period_month=period_month,
        user_visibility_level=user.visibility_level,
        cached_aggregates=aggregates,
        cache_mode=cache_mode,
    )
    # Маркер источника для диагностики (stats.cache='hit' тут означает
    # «агрегат из v2», а не «из cache_history»).
    (result.get("stats") or {}).update({"source": "v2"})
    return result


# ── S20 shadow-mode: фоновое сравнение raw vs v2 ───────────────────
_V2_SHADOW_LAST: dict[tuple[int, str], float] = {}
_V2_SHADOW_MIN_INTERVAL = 1800.0  # сек; раз в полчаса на (ключ, период, метод)
_V2_SHADOW_TASKS: set = set()     # ссылки, чтобы GC не съел таски


def _schedule_v2_shadow(
    *, user_id: int, pf_key_id: int,
    date_start: str, date_end: str,
    period_month: str | None, project_filter: list[str] | None,
    method: str, raw_line_totals: dict[str, float],
) -> None:
    import time as _time
    throttle_key = (pf_key_id, f"{period_month}|{method}|{sorted(project_filter or [])}")
    now = _time.time()
    if now - _V2_SHADOW_LAST.get(throttle_key, 0.0) < _V2_SHADOW_MIN_INTERVAL:
        return
    _V2_SHADOW_LAST[throttle_key] = now
    # Ограничить рост throttle-словаря (ключи с фильтрами множатся).
    if len(_V2_SHADOW_LAST) > 500:
        for k in sorted(_V2_SHADOW_LAST, key=_V2_SHADOW_LAST.get)[:250]:
            _V2_SHADOW_LAST.pop(k, None)
    task = asyncio.get_running_loop().create_task(_run_v2_shadow(
        user_id=user_id, date_start=date_start, date_end=date_end,
        period_month=period_month, project_filter=project_filter,
        method=method, raw_line_totals=raw_line_totals,
    ))
    _V2_SHADOW_TASKS.add(task)
    task.add_done_callback(_V2_SHADOW_TASKS.discard)


async def _run_v2_shadow(
    *, user_id: int, date_start: str, date_end: str,
    period_month: str | None, project_filter: list[str] | None,
    method: str, raw_line_totals: dict[str, float],
) -> None:
    """Фон: построить P&L из v2 с теми же параметрами и сравнить line totals.
    Расхождения (> 1 копейки) — warning в лог с построчной детализацией."""
    import logging
    from .db import get_session_factory
    log = logging.getLogger("uvicorn.error")
    tag = f"v2-shadow {period_month or date_start} {method}"
    try:
        Sm = get_session_factory()
        async with Sm() as session:
            user = await session.get(User, user_id)
            if user is None:
                return
            pf = await planfact_for(session, user)
            v2_result = await _build_pnl_v2_result(
                session=session, user=user, pf=pf,
                date_start=date_start, date_end=date_end,
                period_month=period_month, project_filter=project_filter,
                method=method,
            )
            if v2_result is None:
                log.warning("%s: v2 недоступен (причина выше)", tag)
                return
            v2_totals = {
                str(ln.get("code")): float((ln.get("total") or {}).get("amount") or 0)
                for ln in (v2_result.get("lines") or [])
            }
            diffs: list[str] = []
            for code in sorted(set(raw_line_totals) | set(v2_totals)):
                a = raw_line_totals.get(code, 0.0)
                b = v2_totals.get(code, 0.0)
                if abs(a - b) > 0.01:
                    diffs.append(f"{code}: raw={a:.2f} v2={b:.2f} Δ={a - b:+.2f}")
            if diffs:
                log.warning("%s: РАСХОЖДЕНИЯ в %d строках — %s",
                            tag, len(diffs), "; ".join(diffs[:12]))
            else:
                log.info("%s: OK — %d строк сходятся (|Δ| ≤ 0.01)",
                         tag, len(v2_totals))
    except Exception:
        log.exception("%s: unhandled", tag)


async def _resolve_project_filter(
    session: AsyncSession,
    user_id: int,
    planfact_key_id: int | None,
    project_ids: list[str] | None,
) -> list[str] | None:
    """Эффективный фильтр проектов с учётом прав юзера.

    Allowed-set = активные проекты PF-ключа МИНУС личный hidden-list юзера.
    Явно переданный project_ids ПЕРЕСЕКАЕТСЯ с allowed-set, а не доверяется
    как есть — иначе любой юзер мог запросить скрытую от него точку по id
    (IDOR, см. code-review 2026-06-10, V1).

    Возвращает:
      - None         — фильтра нет (показывать всё; конфиг проектов пуст)
      - [] (пустой)  — юзеру не доступно ничего из запрошенного
      - [ids...]     — итоговый список
    """
    active: set[str] | None = None
    if planfact_key_id is not None:
        active = await store.get_active_project_ids(session, planfact_key_id)
    hidden = await store.get_user_hidden_projects(session, user_id)

    if project_ids:
        requested = {p for p in project_ids if p}
        if active is not None:
            requested &= active
        if hidden:
            requested -= hidden
        # Пустое пересечение = «ничего не доступно», НЕ «без фильтра».
        return sorted(requested)

    if active is None:
        return None
    if hidden:
        active = active - hidden
    return sorted(active) if active else []


def _months_between(date_start: str, date_end: str) -> list[str]:
    """Список 'YYYY-MM' для всех ПОЛНЫХ календарных месяцев в [date_start..date_end].

    Период «март-апрель 2026» (2026-03-01..2026-04-30) → ['2026-03', '2026-04'].
    Частичные месяцы по краям не включаются — фронт всегда передаёт
    full-month диапазоны через monthSelectFrom/To.
    """
    from datetime import date
    from calendar import monthrange
    try:
        d1 = date.fromisoformat(date_start)
        d2 = date.fromisoformat(date_end)
    except ValueError:
        return []
    out: list[str] = []
    y, m = d1.year, d1.month
    while date(y, m, 1) <= d2:
        last_day = monthrange(y, m)[1]
        m_end = date(y, m, last_day)
        m_start = date(y, m, 1)
        if m_start >= d1 and m_end <= d2:
            out.append(f"{y:04d}-{m:02d}")
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


# ── S23: количество заказов из Dodo IS (с LFL год-к-году) для карточек ──
# Каналы Dodo → 2 группы как в Dodo IS UI: «Доставка+самовывоз» и «Ресторан».
_ORDERS_CH_GROUP = {
    "Delivery": "delivery_takeaway",
    "Takeaway": "delivery_takeaway",
    "Dine-in": "restaurant",
}
_ORDERS_CACHE: dict[tuple, tuple[float, dict]] = {}
_ORDERS_TTL_LIVE = 120.0     # период включает сегодня — короткий кэш
_ORDERS_TTL_PAST = 3600.0    # закрытый период immutable — длинный


async def _fetch_monthly_orders(
    token: str, uuids: list[str], date_start: str, date_end: str,
    *, pf_key_id: int, live: bool,
) -> dict[str, dict[str, int]]:
    """{uuid_norm → {total, delivery_takeaway, restaurant}} из Dodo
    /finances/sales/units/monthly за [date_start..date_end]. С TTL-кэшем."""
    import time as _t
    key = (pf_key_id, date_start, date_end, tuple(sorted(uuids)))
    ttl = _ORDERS_TTL_LIVE if live else _ORDERS_TTL_PAST
    hit = _ORDERS_CACHE.get(key)
    if hit and (_t.time() - hit[0]) < ttl:
        return hit[1]
    rows = await dodois_client.fetch_finance_sales_monthly(
        token, uuids, date_start, date_end,
    )
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        u = board_module._normalize_uuid(r.get("unitId", ""))
        if not u:
            continue
        d = out.setdefault(u, {"total": 0, "delivery_takeaway": 0, "restaurant": 0})
        d["total"] += int(r.get("ordersCount") or 0)
        for b in (r.get("salesBreakdown") or []):
            grp = _ORDERS_CH_GROUP.get(b.get("salesChannel"))
            if grp:
                d[grp] += int(b.get("ordersCount") or 0)
    if len(_ORDERS_CACHE) > 500:
        for k in sorted(_ORDERS_CACHE, key=lambda k: _ORDERS_CACHE[k][0])[:250]:
            _ORDERS_CACHE.pop(k, None)
    _ORDERS_CACHE[key] = (_t.time(), out)
    return out


async def _attach_orders(
    *, session: AsyncSession, user: User, result: dict,
    date_start: str, date_end: str, effective_projects: list[str] | None,
) -> None:
    """S23: добавить result['orders'] (per-project + total) с LFL (тот же
    период −1 год). Никогда не бросает — при сбое блок просто отсутствует,
    страница не ломается. Требует привязанных Dodo-юнитов у активных точек."""
    import logging
    from datetime import date as _date
    from .day_window import _shift_year
    log = logging.getLogger("uvicorn.error")
    pf_key_id = user.planfact_key_id
    if pf_key_id is None:
        return
    try:
        cfg = await store.list_projects_config(session, pf_key_id)
        allow = set(effective_projects) if effective_projects is not None else None
        uuid_to_pid: dict[str, str] = {}
        uuids: list[str] = []
        for pid, c in cfg.items():
            uu = c.get("dodo_unit_uuid")
            if not (c.get("is_active") and uu):
                continue
            if allow is not None and pid not in allow:
                continue
            uuid_to_pid[board_module._normalize_uuid(uu)] = pid
            uuids.append(uu)
        if not uuids:
            return
        ly_start = _shift_year(_date.fromisoformat(date_start), -1).isoformat()
        ly_end = _shift_year(_date.fromisoformat(date_end), -1).isoformat()
        live = date_end >= _date.today().isoformat()
        token = await get_dodois_token(session, user)
        cur, prev = await asyncio.gather(
            _fetch_monthly_orders(
                token, uuids, date_start, date_end, pf_key_id=pf_key_id, live=live),
            _fetch_monthly_orders(
                token, uuids, ly_start, ly_end, pf_key_id=pf_key_id, live=False),
        )
    except Exception:
        log.exception("S23 orders: fetch failed — пропускаем блок заказов")
        return

    def _blk(cd: dict, pd: dict, field: str) -> dict:
        v = int((cd or {}).get(field, 0))
        p = int((pd or {}).get(field, 0))
        return {"value": v, "prev": p, "delta_pct": (v / p - 1.0) if p > 0 else None}

    def _full(cd: dict, pd: dict) -> dict:
        b = _blk(cd, pd, "total")
        b["channels"] = {
            "delivery_takeaway": _blk(cd, pd, "delivery_takeaway"),
            "restaurant": _blk(cd, pd, "restaurant"),
        }
        return b

    projects: dict[str, dict] = {}
    tot_c = {"total": 0, "delivery_takeaway": 0, "restaurant": 0}
    tot_p = {"total": 0, "delivery_takeaway": 0, "restaurant": 0}
    for u, pid in uuid_to_pid.items():
        cd, pd = cur.get(u) or {}, prev.get(u) or {}
        for f in tot_c:
            tot_c[f] += int(cd.get(f, 0))
            tot_p[f] += int(pd.get(f, 0))
        projects[pid] = _full(cd, pd)
    result["orders"] = {"projects": projects, "total": _full(tot_c, tot_p)}


@app.get("/api/pnl")
async def get_pnl(
    date_start: str = Query(..., description="YYYY-MM-DD"),
    date_end: str = Query(..., description="YYYY-MM-DD"),
    project_ids: list[str] | None = Query(None),
    compare_start: str | None = Query(None),
    compare_end: str | None = Query(None),
    compare_mode: str = Query("lfl", regex="^(lfl|mom)$"),
    method: str = Query("accrual", regex="^(accrual|cash)$"),
    period_month: str | None = Query(None, description="'YYYY-MM'. Если не задан — выводится из дат."),
    group_by: str | None = Query(None, regex="^(month)$",
        description="Если 'month' — добавляет помесячный breakdown в response.monthly."),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    effective_projects = await _resolve_project_filter(
        session, user.id, user.planfact_key_id, project_ids,
    )
    if effective_projects is not None and len(effective_projects) == 0:
        # Динамический targetable_metrics (как в build_pnl): из pnl_metrics
        # WHERE is_target=true, чтобы фронт даже в пустом ответе знал,
        # какие колонки целей рисовать в матрице настроек.
        _pf_metrics_empty = (
            await store.list_metrics(session, user.planfact_key_id)
            if user.planfact_key_id else []
        )
        _targetable_empty = [
            m["code"] for m in _pf_metrics_empty if m.get("is_target")
        ]
        return {
            "projects": [], "lines": [], "template_lines": [], "targets": [],
            "category_breakdown": [], "revenue_by_channel": {}, "unclassified": [],
            "pnl_codes": pnl_module.PNL_CODES,
            "targetable_metrics": _targetable_empty,
            "computed_targetable_metrics": sorted(_targetable_empty),
            "denominators": pnl_module.DENOMINATOR,
            "method": method, "period_month": period_month, "stats": {},
            "default_targets": (
                # S14.3: учитываем period_month для эффективных таргетов.
                (await store.effective_targets_for_period(
                    session, user.planfact_key_id, period_month,
                ))[1]
                if user.planfact_key_id else {}
            ),
            "ops_targets": (
                (await store.effective_ops_targets_for_period(
                    session, user.planfact_key_id, period_month,
                ))[0]
                if user.planfact_key_id else {}
            ),
            "ops_metrics_meta": store.OPS_METRICS,
            "period": {"current": {"start": date_start, "end": date_end}},
        }

    pm = period_month or _derive_period_month(date_start, date_end)

    pf = await planfact_for(session, user)
    try:
        result = await _build_pnl_for_period(
            session=session, user=user, pf=pf,
            date_start=date_start, date_end=date_end, period_month=pm,
            project_filter=effective_projects, method=method,
        )
        if compare_start and compare_end:
            prev_pm = _derive_period_month(compare_start, compare_end)
            prev = await _build_pnl_for_period(
                session=session, user=user, pf=pf,
                date_start=compare_start, date_end=compare_end,
                period_month=prev_pm,
                project_filter=effective_projects, method=method,
            )
            result = pnl_module.compare_pnl(result, prev, mode=compare_mode)
            result["period"] = {
                "current": {"start": date_start, "end": date_end},
                "previous": {"start": compare_start, "end": compare_end},
                "compare_mode": compare_mode,
            }
        else:
            result["period"] = {"current": {"start": date_start, "end": date_end}}
    except PlanFactError as e:
        raise HTTPException(502, str(e))

    # S11.9: проставляем флаг «синк сейчас идёт» — берётся из памяти процесса
    # (set _OPS_SYNC_INFLIGHT). Делаем тут, а не в pnl.py, чтобы не импортить
    # main → pnl циклически.
    if (
        user.planfact_key_id and pm
        and result.get("ops_freshness") is not None
        and is_ops_sync_running(user.planfact_key_id, pm)
    ):
        result["ops_freshness"]["is_syncing"] = True

    # S13.2: помесячный breakdown. Запрашивается фронтом в режиме «Период»
    # (group_by=month). Каждый месяц считается отдельным вызовом
    # _build_pnl_for_period — он уже умеет читать cache_history для
    # замороженных и держит result-LRU, поэтому повторные запросы быстры.
    if group_by == "month":
        try:
            months_in_range = _months_between(date_start, date_end)
        except Exception:
            months_in_range = []
        # Собираем {month: {by_node: {nodeId: amount}, by_code: {code: amount}}};
        # project breakdown в Period не показываем — на фронте детализация
        # группируется по месяцам. by_node нужен для template-tree (узлы без
        # pnl_code), by_code — для агрегатной таблицы.
        #
        # Параллелим месяцы через gather + Semaphore(3): PF-API чувствителен
        # к нагрузке (rate-limit), 3 одновременных запроса — компромисс между
        # latency и риском быть забаненным. Для 6 месяцев это ~2x ускорение.
        sem = asyncio.Semaphore(3)

        async def _fetch_month(m: str) -> tuple[str, dict]:
            ms, me = _month_range_dates(m)
            async with sem:
                month_result = await _build_pnl_for_period(
                    session=session, user=user, pf=pf,
                    date_start=ms, date_end=me, period_month=m,
                    project_filter=effective_projects, method=method,
                )
            by_node: dict[str, float | None] = {}
            for tn in month_result.get("template_lines", []) or []:
                nid = tn.get("id")
                if nid is not None:
                    by_node[str(nid)] = (tn.get("total") or {}).get("amount")
            by_code: dict[str, float | None] = {
                ln["code"]: (ln.get("total") or {}).get("amount")
                for ln in month_result.get("lines", []) or []
            }
            return m, {"by_node": by_node, "by_code": by_code}

        try:
            month_results = await asyncio.gather(
                *(_fetch_month(m) for m in months_in_range)
            )
        except PlanFactError as e:
            raise HTTPException(502, str(e))
        monthly_map: dict[str, dict] = {m: data for (m, data) in month_results}
        result["monthly"] = monthly_map
        result["months_in_range"] = months_in_range

    # S23: блок «Заказы» с LFL (год к году) из Dodo IS. Graceful — при сбое
    # отсутствует, страница работает.
    await _attach_orders(
        session=session, user=user, result=result,
        date_start=date_start, date_end=date_end,
        effective_projects=effective_projects,
    )

    return result


def _month_range_dates(month_key: str) -> tuple[str, str]:
    """'YYYY-MM' → ('YYYY-MM-01', 'YYYY-MM-<last>')."""
    from calendar import monthrange
    y, m = int(month_key[:4]), int(month_key[5:7])
    return f"{month_key}-01", f"{month_key}-{monthrange(y, m)[1]:02d}"


def _human_period_label(date_start: str, date_end: str) -> str:
    """Человекочитаемый период для xlsx-шапки.

    Один месяц → 'Апрель 2026 г.'
    Несколько → 'Март – Апрель 2026'
    Произвольно → '01.03.2026 – 30.04.2026'
    """
    months_ru = [
        "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
    ]
    try:
        from datetime import date as _date
        d1 = _date.fromisoformat(date_start)
        d2 = _date.fromisoformat(date_end)
    except Exception:
        return f"{date_start} – {date_end}"
    months = _months_between(date_start, date_end)
    if len(months) == 1:
        y, m = months[0].split("-")
        return f"{months_ru[int(m) - 1]} {y} г."
    if len(months) >= 2:
        ys, ms = months[0].split("-")
        ye, me = months[-1].split("-")
        if ys == ye:
            return f"{months_ru[int(ms) - 1]} – {months_ru[int(me) - 1]} {ys}"
        return f"{months_ru[int(ms) - 1]} {ys} – {months_ru[int(me) - 1]} {ye}"
    return f"{date_start} – {date_end}"


@app.get("/api/pnl.xlsx")
async def get_pnl_xlsx(
    date_start: str = Query(..., description="YYYY-MM-DD"),
    date_end: str = Query(..., description="YYYY-MM-DD"),
    project_ids: list[str] | None = Query(None),
    method: str = Query("accrual", regex="^(accrual|cash)$"),
    period_month: str | None = Query(None),
    group_by: str | None = Query(None, regex="^(month)$"),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Скачать детализацию P&L в xlsx (S13.5).

    Параметры идентичны /api/pnl. Логика сборки данных та же — переиспользуем
    _build_pnl_for_period (включая cache_history и LRU). Затем рендерим
    xlsx через xlsx_export.render_pnl_xlsx.
    """
    from . import xlsx_export

    effective_projects = await _resolve_project_filter(
        session, user.id, user.planfact_key_id, project_ids,
    )
    if effective_projects is not None and len(effective_projects) == 0:
        raise HTTPException(400, "Не выбрано ни одной пиццерии.")

    pm = period_month or _derive_period_month(date_start, date_end)
    pf = await planfact_for(session, user)
    try:
        result = await _build_pnl_for_period(
            session=session, user=user, pf=pf,
            date_start=date_start, date_end=date_end, period_month=pm,
            project_filter=effective_projects, method=method,
        )
        is_period_mode = group_by == "month"
        if is_period_mode:
            months_in_range = _months_between(date_start, date_end)
            monthly_map: dict[str, dict] = {}
            for m in months_in_range:
                ms, me = _month_range_dates(m)
                month_result = await _build_pnl_for_period(
                    session=session, user=user, pf=pf,
                    date_start=ms, date_end=me, period_month=m,
                    project_filter=effective_projects, method=method,
                )
                by_node = {}
                for tn in month_result.get("template_lines") or []:
                    nid = tn.get("id")
                    if nid is not None:
                        by_node[str(nid)] = (tn.get("total") or {}).get("amount")
                by_code = {
                    ln["code"]: (ln.get("total") or {}).get("amount")
                    for ln in month_result.get("lines") or []
                }
                monthly_map[m] = {"by_node": by_node, "by_code": by_code}
            result["monthly"] = monthly_map
            result["months_in_range"] = months_in_range
    except PlanFactError as e:
        raise HTTPException(502, str(e))

    period_label = _human_period_label(date_start, date_end)
    project_names_map = {p["id"]: p.get("name") or str(p["id"]) for p in result.get("projects") or []}
    selected_names = [project_names_map.get(pid, pid) for pid in (effective_projects or [])]

    blob = xlsx_export.render_pnl_xlsx(
        pnl=result,
        project_names=project_names_map,
        period_label=period_label,
        selected_project_names=selected_names,
        method=method,
        is_period_mode=is_period_mode,
    )
    # ASCII-имя для Content-Disposition: pnl-2026-04.xlsx или pnl-2026-03_2026-04.xlsx
    iso_period = (
        date_start[:7] if date_start[:7] == date_end[:7]
        else f"{date_start[:7]}_{date_end[:7]}"
    )
    fname = xlsx_export.make_filename("pnl", iso_period)
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/revenue-history")
async def get_revenue_history(
    anchor: str = Query(..., description="'YYYY-MM' — последний месяц окна"),
    months: int = Query(12, ge=1, le=36),
    project_ids: list[str] | None = Query(None),
    include_ly: bool = Query(False),
    method: str = Query("accrual", regex="^(accrual|cash)$"),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Выручка по месяцам за окно [anchor-months+1 .. anchor], опционально + LFL (тот же месяц годом ранее)."""
    effective_projects = await _resolve_project_filter(
        session, user.id, user.planfact_key_id, project_ids,
    )
    if effective_projects is not None and len(effective_projects) == 0:
        return {"months": [], "totals": {}, "projects": {}, "project_names": {}}

    period_months = pnl_module.month_range(anchor, months)
    date_start = f"{period_months[0]}-01"
    last_y, last_m = (int(x) for x in period_months[-1].split("-"))
    from calendar import monthrange
    last_day = monthrange(last_y, last_m)[1]
    date_end = f"{period_months[-1]}-{last_day:02d}"

    pf = await planfact_for(session, user)
    try:
        # S9.3: для замороженных месяцев читаем revenue_by_channel из
        # cache_history, в PF идём только за live-окном.
        cur = await _build_revenue_history_window(
            session=session, user=user, pf=pf,
            period_months=period_months,
            project_filter=effective_projects, method=method,
        )

        out: dict = {
            "months": cur["months"],
            "totals": cur["totals"],
            "by_channel": cur["by_channel"],
            "projects": cur["projects"],
            "project_names": cur["project_names"],
            "period": {"start": date_start, "end": date_end},
        }

        if include_ly:
            ly_anchor_y, ly_anchor_m = (int(x) for x in anchor.split("-"))
            ly_anchor = f"{ly_anchor_y - 1:04d}-{ly_anchor_m:02d}"
            ly_months = pnl_module.month_range(ly_anchor, months)
            ly_start = f"{ly_months[0]}-01"
            ly_y, ly_m = (int(x) for x in ly_months[-1].split("-"))
            ly_last_day = monthrange(ly_y, ly_m)[1]
            ly_end = f"{ly_months[-1]}-{ly_last_day:02d}"
            ly = await _build_revenue_history_window(
                session=session, user=user, pf=pf,
                period_months=ly_months,
                project_filter=effective_projects, method=method,
            )
            out["ly"] = {
                "months": ly["months"],
                "totals": ly["totals"],
                "by_channel": ly["by_channel"],
                "period": {"start": ly_start, "end": ly_end},
            }
    except PlanFactError as e:
        raise HTTPException(502, str(e))
    return out


@app.get("/api/operations")
async def get_operations(
    date_start: str,
    date_end: str,
    project_id: str | None = None,
    project_ids: list[str] = Query(default_factory=list),
    category_id: str | None = None,
    category_ids: list[str] = Query(default_factory=list),
    offset: int = 0,
    limit: int = 100,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    pf = await planfact_for(session, user)
    # Объединяем legacy single category_id и новый список category_ids.
    cat_id_set: set[str] = set()
    if category_id:
        cat_id_set.add(category_id)
    for c in (category_ids or []):
        if c:
            cat_id_set.add(c)
    cat_ids_list: list[str] | None = sorted(cat_id_set) if cat_id_set else None

    # Объединяем legacy single project_id и новый список project_ids.
    # Если ни одного — фильтра нет (Backend отдаст все проекты ключа).
    proj_id_set: set[str] = set()
    if project_id:
        proj_id_set.add(project_id)
    for p in (project_ids or []):
        if p:
            proj_id_set.add(p)
    proj_ids_list: list[str] | None = sorted(proj_id_set) if proj_id_set else None

    # Права: проекты через allowed-set юзера, категории по visibility_level.
    proj_ids_list = await _authorize_operations_drilldown(
        session, user, pf, proj_ids_list, cat_ids_list,
    )
    if proj_ids_list is not None and len(proj_ids_list) == 0:
        return {
            "items": [], "total": 0, "raw_count": 0,
            "filtered_count": 0, "sum_value": 0.0,
        }

    try:
        data = await pf.list_operations(
            date_start=date_start,
            date_end=date_end,
            project_ids=proj_ids_list,
            category_ids=cat_ids_list,
            offset=offset,
            limit=limit,
        )
    except PlanFactError as e:
        raise HTTPException(502, str(e))

    # Нормализуем операции: фильтруем operationParts по project_ids и category_ids.
    norm, sum_value, raw_count = _normalize_operation_parts(
        data.get("items") or [], proj_ids_list, cat_ids_list,
    )
    return {
        "items": norm,
        "total": data.get("total"),
        "raw_count": raw_count,
        "filtered_count": len(norm),
        "sum_value": sum_value,
    }


def _normalize_operation_parts(
    items: list[dict],
    proj_ids_list: list[str] | None,
    cat_ids_list: list[str] | None,
) -> tuple[list[dict], float, int]:
    """Достаём операции из PlanFact-ответа, фильтруем по проектам/категориям,
    разворачиваем operationParts в плоский список и считаем сумму.

    Возвращаем (norm_items, sum_value, raw_count). Используется и в
    /api/operations (JSON), и в /api/operations.xlsx.
    """
    proj_filter_set = set(proj_ids_list) if proj_ids_list else None
    cat_ids_set = set(cat_ids_list) if cat_ids_list else None
    norm: list[dict] = []
    sum_value = 0.0
    for op in items:
        parts = op.get("operationParts") or []
        if proj_filter_set:
            parts = [p for p in parts if str((p.get("project") or {}).get("projectId")) in proj_filter_set]
        if cat_ids_set:
            parts = [
                p for p in parts
                if str((p.get("operationCategory") or {}).get("operationCategoryId")) in cat_ids_set
            ]
        op_type = op.get("operationType") or ""
        sign = -1 if op_type == "Outcome" else 1
        for p in parts:
            raw_v = p.get("value") if p.get("value") is not None else op.get("value")
            try:
                v = float(raw_v) if raw_v is not None else 0.0
            except (TypeError, ValueError):
                v = 0.0
            signed = sign * v
            sum_value += signed
            norm.append({
                "operationId": op.get("operationId"),
                "date": op.get("operationDate"),
                "type": op_type,
                "value": signed,
                "comment": op.get("comment"),
                "project": (p.get("project") or {}).get("title"),
                "category": (p.get("operationCategory") or {}).get("title"),
                "contrAgent": (p.get("contrAgent") or {}).get("title"),
            })
    return norm, sum_value, len(items)


async def _authorize_operations_drilldown(
    session: AsyncSession,
    user: User,
    pf,
    proj_ids_list: list[str] | None,
    cat_ids_list: list[str] | None,
) -> list[str] | None:
    """Применить права юзера к параметрам drill-down /api/operations{,.xlsx}.

    1. Проекты — через _resolve_project_filter (active − hidden, explicit
       список пересекается с allowed-set).
    2. Категории — если pnl_code категории требует min_visibility_level выше
       уровня юзера (та же логика, что _filter_lines_by_visibility в pnl.py),
       → 403. Иначе юзер 10-го уровня делал бы drill-down по DIVIDENDS/TAX/
       MGMT, скрытым из его P&L (code-review 2026-06-10, V2).

    Возвращает эффективный список проектов (None = без фильтра).
    """
    effective_projects = await _resolve_project_filter(
        session, user.id, user.planfact_key_id, proj_ids_list,
    )
    if cat_ids_list:
        user_level = int(user.visibility_level or 0)
        categories = await pf.list_operation_categories()
        cat_index = await pnl_module._build_category_index(
            session, user.id, user.planfact_key_id, categories,
        )
        metrics = (
            await store.list_metrics(session, user.planfact_key_id)
            if user.planfact_key_id else []
        )
        min_level_by_code = {
            m["code"]: int(m.get("min_visibility_level") or 0)
            for m in metrics
        }
        for cid in cat_ids_list:
            code = (cat_index.get(str(cid)) or {}).get("pnl_code")
            if not code:
                continue  # неклассифицированные видны всем (как в P&L)
            min_level = min_level_by_code.get(
                code, pnl_module.LINE_CODE_DEFAULT_MIN_LEVEL.get(code, 0),
            )
            if min_level > user_level:
                raise HTTPException(
                    403, "Недостаточно прав для просмотра операций этой статьи.",
                )
    return effective_projects


@app.get("/api/operations.xlsx")
async def get_operations_xlsx(
    date_start: str,
    date_end: str,
    project_id: str | None = None,
    project_ids: list[str] = Query(default_factory=list),
    category_id: str | None = None,
    category_ids: list[str] = Query(default_factory=list),
    label: str | None = Query(None, description="Название статьи/категории — попадёт в шапку и имя файла"),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Скачать список операций в xlsx (S13.6). Те же параметры что у /api/operations,
    плюс label для пользовательского контекста (статья / итог)."""
    from . import xlsx_export

    pf = await planfact_for(session, user)
    cat_id_set: set[str] = set()
    if category_id:
        cat_id_set.add(category_id)
    for c in (category_ids or []):
        if c:
            cat_id_set.add(c)
    cat_ids_list: list[str] | None = sorted(cat_id_set) if cat_id_set else None

    proj_id_set: set[str] = set()
    if project_id:
        proj_id_set.add(project_id)
    for p in (project_ids or []):
        if p:
            proj_id_set.add(p)
    proj_ids_list: list[str] | None = sorted(proj_id_set) if proj_id_set else None

    # Права: проекты через allowed-set юзера, категории по visibility_level.
    proj_ids_list = await _authorize_operations_drilldown(
        session, user, pf, proj_ids_list, cat_ids_list,
    )
    if proj_ids_list is not None and len(proj_ids_list) == 0:
        raise HTTPException(400, "Не выбрано ни одной доступной пиццерии.")

    try:
        # Достаём с большим limit, чтобы влезли все операции одного периода.
        # Pages у PF тут уже есть пагинация, но для drill-down 5000 хватает.
        data = await pf.list_operations(
            date_start=date_start, date_end=date_end,
            project_ids=proj_ids_list, category_ids=cat_ids_list,
            offset=0, limit=5000,
        )
    except PlanFactError as e:
        raise HTTPException(502, str(e))

    norm, sum_value, _ = _normalize_operation_parts(
        data.get("items") or [], proj_ids_list, cat_ids_list,
    )

    # Имена проектов для подписи. Если выбран один — берём его имя из PF.
    project_label = "Все выбранные"
    if proj_ids_list and len(proj_ids_list) == 1:
        try:
            projects = await pf.list_projects()
            for p in projects:
                pid = str(p.get("projectId") or p.get("id") or "")
                if pid == proj_ids_list[0]:
                    project_label = p.get("title") or p.get("name") or pid
                    break
        except PlanFactError:
            project_label = proj_ids_list[0]

    blob = xlsx_export.render_operations_xlsx(
        items=norm,
        sum_value=sum_value,
        period_label=_human_period_label(date_start, date_end),
        project_label=project_label,
        category_label=label or "—",
    )
    iso_period = (
        date_start[:7] if date_start[:7] == date_end[:7]
        else f"{date_start[:7]}_{date_end[:7]}"
    )
    fname = xlsx_export.make_filename("operations", iso_period)
    return Response(
        content=blob,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# /api/refresh удалён в S3.6: in-memory PlanFact-кэш истекает по TTL
# (config.cache_ttl, по умолчанию 60 мин), а закрытые месяцы лежат
# в cache_history (инвалидируются админом через «Переоткрыть»).
# Кнопка «Обновить P&L» убрана — TTL покрывает кейсы.


# --- Targets CRUD ---
# Таргеты привязаны к planfact_key (метрики UC/LC/DC общие на ключ).
# Read разрешаем любому юзеру (читают все, кому нужно), write — admin only.

@app.get("/api/targets")
async def list_targets(
    project_id: str | None = None,
    period_month: str = Query("__default__"),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    if not user.planfact_key_id:
        return {"targets": []}
    return {"targets": await store.list_targets(
        session, user.planfact_key_id, project_id,
        period_month=period_month,
    )}


@app.post("/api/targets")
async def upsert_target(
    payload: TargetIn,
    user: User = Depends(require_territorial),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.upsert_target(
        session, pf_key_id, payload.project_id,
        payload.metric_code, payload.target_pct,
        period_month=payload.period_month,
    )
    return {"status": "ok"}


@app.delete("/api/targets")
async def delete_target(
    project_id: str, metric_code: str,
    period_month: str = Query("__default__"),
    user: User = Depends(require_territorial),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.delete_target(
        session, pf_key_id, project_id, metric_code, period_month=period_month,
    )
    return {"status": "ok"}


# --- Default targets (fallback для всех проектов) ---

@app.get("/api/targets/defaults")
async def list_default_targets(
    period_month: str = Query("__default__"),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    if not user.planfact_key_id:
        return {"defaults": {}}
    return {"defaults": await store.list_default_targets(
        session, user.planfact_key_id, period_month=period_month,
    )}


@app.post("/api/targets/defaults")
async def upsert_default_target(
    payload: DefaultTargetIn,
    user: User = Depends(require_territorial),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.upsert_default_target(
        session, pf_key_id, payload.metric_code, payload.target_pct,
        period_month=payload.period_month,
    )
    return {"status": "ok"}


@app.delete("/api/targets/defaults")
async def delete_default_target(
    metric_code: str,
    period_month: str = Query("__default__"),
    user: User = Depends(require_territorial),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.delete_default_target(
        session, pf_key_id, metric_code, period_month=period_month,
    )
    return {"status": "ok"}


# --- App settings ---

@app.get("/api/settings")
async def get_settings(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    return {"settings": await store.list_settings(session, user.id)}


@app.post("/api/settings")
async def set_settings(
    payload: SettingIn,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.set_setting(session, user.id, payload.key, payload.value)
    invalidate_planfact_for(user.id)
    return {"status": "ok"}


# --- Projects config (активность / имя / сортировка) ---

@app.get("/api/projects/config")
async def get_projects_config(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    if not user.planfact_key_id:
        return {"config": {}}
    return {"config": await store.list_projects_config(session, user.planfact_key_id)}


@app.post("/api/projects/config")
async def upsert_projects_config(
    payload: ProjectConfigIn,
    # Только администратор может архивировать проекты, переименовывать и
    # подвязывать Dodo IS unit. Обычный юзер видит итог через GET и пользуется
    # сайдбар-чекбоксами как сессионным фильтром.
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    kwargs: dict = {
        "is_active": payload.is_active,
        "display_name": payload.display_name,
        "sort_order": payload.sort_order,
    }
    if "dodo_unit_uuid" in payload.model_fields_set:
        kwargs["dodo_unit_uuid"] = payload.dodo_unit_uuid
    await store.upsert_project_config(session, pf_key_id, payload.project_id, **kwargs)
    invalidate_planfact_for(user.id)
    return {"status": "ok"}


# --- Ops metrics (ручной ввод на /settings) ---

@app.get("/api/ops-metrics")
async def get_ops_metrics(
    period_month: str | None = Query(None, description="'YYYY-MM'. Если не задан — все месяцы."),
    project_id: str | None = None,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    if not user.planfact_key_id:
        return {"metrics": {}, "meta": store.OPS_METRICS, "targets": {}}
    return {
        "metrics": await store.list_ops_metrics(
            session, user.planfact_key_id,
            period_month=period_month, project_id=project_id,
        ),
        "meta": store.OPS_METRICS,
        "targets": await store.list_ops_targets(session, user.planfact_key_id),
    }


@app.post("/api/ops-metrics")
async def upsert_ops_metric(
    payload: OpsMetricIn,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.upsert_ops_metric(
        session, pf_key_id, payload.project_id, payload.period_month,
        orders_per_courier_h=payload.orders_per_courier_h,
        products_per_h=payload.products_per_h,
        revenue_per_person_h=payload.revenue_per_person_h,
    )
    return {"status": "ok"}


@app.delete("/api/ops-metrics")
async def delete_ops_metric(
    project_id: str, period_month: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.delete_ops_metric(session, pf_key_id, project_id, period_month)
    return {"status": "ok"}


# --- Ops targets (глобальные цели по ops-метрикам, на уровне PF-ключа) ---

@app.get("/api/ops-targets")
async def list_ops_targets_ep(
    period_month: str = Query("__default__"),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    if not user.planfact_key_id:
        return {"targets": {}, "project_targets": [], "meta": store.OPS_METRICS}
    return {
        "targets": await store.list_ops_targets(
            session, user.planfact_key_id, period_month=period_month,
        ),
        "project_targets": await store.list_ops_project_targets(
            session, user.planfact_key_id, period_month=period_month,
        ),
        "meta": store.OPS_METRICS,
    }


@app.post("/api/ops-targets")
async def upsert_ops_target_ep(
    payload: OpsTargetIn,
    user: User = Depends(require_territorial),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.upsert_ops_target(
        session, pf_key_id, payload.metric_code, payload.target_value,
        period_month=payload.period_month,
    )
    return {"status": "ok"}


@app.delete("/api/ops-targets")
async def delete_ops_target_ep(
    metric_code: str,
    period_month: str = Query("__default__"),
    user: User = Depends(require_territorial),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.delete_ops_target(
        session, pf_key_id, metric_code, period_month=period_month,
    )
    return {"status": "ok"}


@app.post("/api/ops-targets/project")
async def upsert_ops_project_target_ep(
    payload: OpsProjectTargetIn,
    user: User = Depends(require_territorial),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.upsert_ops_project_target(
        session, pf_key_id, payload.project_id,
        payload.metric_code, payload.target_value,
        period_month=payload.period_month,
    )
    return {"status": "ok"}


@app.delete("/api/ops-targets/project")
async def delete_ops_project_target_ep(
    project_id: str, metric_code: str,
    period_month: str = Query("__default__"),
    user: User = Depends(require_territorial),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.delete_ops_project_target(
        session, pf_key_id, project_id, metric_code,
        period_month=period_month,
    )
    return {"status": "ok"}


# --- Dodo IS ---

@app.get("/api/dodois/units")
async def dodois_units(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Список юнитов пользователя из Dodo IS. Токен резолвится из
    public.dodois_credentials по user.dodois_credentials_name. На 401
    делаем один retry с force-refresh токена."""
    try:
        units = await with_dodois_retry(session, user, dodois_client.fetch_units)
    except DodoISError as e:
        raise HTTPException(502, str(e))
    pizzerias = [u for u in units if u.get("unitType") == 1]
    return {"units": pizzerias, "all": units}


# Какие (planfact_key_id, period) сейчас синхронизируются — чтобы блокировать
# дубль-запуски и показывать «идёт синхронизация» в UI. Лежит в памяти процесса:
# при рестарте сервиса пропадёт (и это ок — фоновый task тоже умрёт вместе с
# процессом). Если хотим persist — это уже отдельная история.
_OPS_SYNC_INFLIGHT: set[tuple[int, str]] = set()


def is_ops_sync_running(planfact_key_id: int, period: str) -> bool:
    return (planfact_key_id, period) in _OPS_SYNC_INFLIGHT


# ─── /api/board — сетевая сводка дня ───────────────────────────────
# In-memory кэш с TTL per-layer. Ключ — (planfact_key_id, hour_floor_iso,
# filter_key). Внутри — payload и unix timestamp создания.
#
# B6 (code-review 2026-06-10): per-key asyncio.Lock — конкурентные юзеры
# одного ключа с одинаковой выборкой ждут ОДНУ сборку (раньше каждый
# запускал свой fan-out в Dodo IS — задача #3 бэклога); рост в пределах
# часа ограничен _BOARD_CACHE_MAX_VARIANTS фильтр-вариаций на ключ.
_BOARD_CACHE: dict[tuple[int, str, tuple], tuple[float, dict]] = {}
_BOARD_CACHE_TTL = 60  # секунд для live-слоя; вне часа — полная перегенерация
_BOARD_CACHE_MAX_VARIANTS = 8  # максимум filter-вариаций на (pf_key, час)
_BOARD_LOCKS: dict[tuple[int, str, tuple], asyncio.Lock] = {}


@app.get("/api/board")
async def get_board(
    project_ids: list[str] | None = Query(None),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Сводка дня по выбранным проектам.

    Фильтр проектов (как в /api/pnl):
      - Если `project_ids` явно передан — берём этот список (после проверки
        что юзер имеет к ним доступ).
      - Иначе — все активные проекты PF-ключа МИНУС юзерский hidden-list.

    Доступно с любого visibility_level: управляющий видит свою точку,
    территориальный — свои, сетевой — все. Сетевой scoreboard получается
    автоматически когда у юзера все точки выбраны.
    """
    import time
    from .day_window import now_msk

    if not user.planfact_key_id:
        raise HTTPException(400, "У пользователя не задан ключ PlanFact.")

    pf_key_id = user.planfact_key_id

    # Резолвим фильтр: explicit list ∨ visibility - hidden
    resolved_pids = await _resolve_project_filter(
        session, user.id, pf_key_id, project_ids,
    )

    # ── собираем список доступных проектов ──
    cfg = await store.list_projects_config(session, pf_key_id)
    projects: list[tuple[str, str, str]] = []
    for pid, c in cfg.items():
        if not c.get("is_active"):
            continue
        uuid = c.get("dodo_unit_uuid")
        if not uuid:
            continue
        # Применяем фильтр (если задан — иначе пропускаем все)
        if resolved_pids is not None and pid not in resolved_pids:
            continue
        name = c.get("display_name") or pid
        projects.append((pid, name, uuid))

    # Cache key включает фильтр — разные пользователи/выборки получают
    # разные cached payloads. Сортируем для стабильности ключа.
    now = now_msk()
    hour_iso = now.replace(minute=0, second=0, microsecond=0).isoformat()
    filter_key = tuple(sorted(p[0] for p in projects))
    cache_key = (pf_key_id, hour_iso, filter_key)

    if not projects:
        return {"now_msk": now.isoformat(), "projects": [], "totals": {}}

    # Fast-path без локa: свежий кэш отдаём сразу.
    hit = _BOARD_CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _BOARD_CACHE_TTL:
        return hit[1]

    # ── токен Dodo IS ── (до лока: дешёвый DB-read)
    token = await get_dodois_token(session, user)

    lock = _BOARD_LOCKS.setdefault(cache_key, asyncio.Lock())
    async with lock:
        # Re-check внутри лока: пока ждали, сосед мог уже собрать payload.
        hit = _BOARD_CACHE.get(cache_key)
        if hit and (time.time() - hit[0]) < _BOARD_CACHE_TTL:
            return hit[1]

        # ── основная сборка ──
        try:
            payload = await board_module.build_board_payload(
                session=session, token=token,
                planfact_key_id=pf_key_id,
                projects=projects, now=now,
            )
        except (DodoISError, NoTokenError) as e:
            raise HTTPException(502, f"Dodo IS: {e}")

        _BOARD_CACHE[cache_key] = (time.time(), payload)

        # Чистка: (а) другие часы этого PF-ключа; (б) cap на число
        # filter-вариаций в ТЕКУЩЕМ часу (выкидываем самые старые);
        # (в) осиротевшие локи.
        same_hour: list[tuple[float, tuple]] = []
        for k in list(_BOARD_CACHE.keys()):
            if k[0] != pf_key_id:
                continue
            if k[1] != hour_iso:
                _BOARD_CACHE.pop(k, None)
            else:
                same_hour.append((_BOARD_CACHE[k][0], k))
        if len(same_hour) > _BOARD_CACHE_MAX_VARIANTS:
            same_hour.sort()  # старые первыми
            for _, k in same_hour[:-_BOARD_CACHE_MAX_VARIANTS]:
                _BOARD_CACHE.pop(k, None)
        for k in list(_BOARD_LOCKS.keys()):
            if k not in _BOARD_CACHE and k != cache_key and not _BOARD_LOCKS[k].locked():
                _BOARD_LOCKS.pop(k, None)

    return payload


@app.get("/api/ops-metrics/sync-status")
async def ops_sync_status(
    period: str = Query(..., description="'YYYY-MM'"),
    user: User = Depends(require_user),
):
    """Лёгкий статус фонового ops-синка для поллинга на фронте.

    Читает только in-memory флаг _OPS_SYNC_INFLIGHT — НЕ дёргает PlanFact и
    НЕ собирает P&L. Фронт во время синка опрашивает этот endpoint вместо
    тяжёлого /api/pnl, а полную перерисовку делает один раз по завершении.
    """
    if not user.planfact_key_id:
        return {"is_syncing": False}
    return {"is_syncing": is_ops_sync_running(user.planfact_key_id, period)}


@app.post("/api/ops-metrics/sync")
async def sync_ops_metrics_from_dodois(
    period: str = Query(..., description="'YYYY-MM' — месяц, для которого тянем ops"),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Кнопка «⟳ Обновить»: запускает синк ops-метрик из Dodo IS в фоне (S11.9)
    + сбрасывает PlanFact-кэш для пользователя + (для closed-месяцев)
    инвалидирует snapshot в cache_history — чтобы при следующем /api/pnl
    данные пересобрались живьём из PlanFact.

    POST возвращается мгновенно (202 scheduled), не дожидаясь синка ops.
    Прогресс ops юзер видит через ops_freshness.is_syncing в /api/pnl,
    готовность — по обновившемуся last_synced_at. PnL уже сразу будет
    свежий, потому что in-memory кэш PF и snapshot закрытого месяца уже
    инвалидированы синхронно до возврата.
    """
    import asyncio as _asyncio
    from datetime import datetime

    try:
        y, m = map(int, period.split("-"))
        datetime(y, m, 1)  # просто валидация
    except Exception:
        raise HTTPException(400, "period должен быть 'YYYY-MM'")

    if not user.planfact_key_id:
        raise HTTPException(400, "У пользователя не задан ключ PlanFact.")

    # ── 1. Инвалидация PlanFact-кэша для этого пользователя ──
    # In-memory LRU в PlanFactClient держит ответы /operations до cache_ttl
    # (1ч по дефолту). После любой правки в PlanFact юзер хочет видеть
    # свежие данные немедленно — поэтому сбрасываем кэш синхронно.
    invalidate_planfact_for(user.id)

    # ── 2. Если за этот period есть snapshot — удалить ──
    # Для closed-месяца build_pnl возвращает payload из cache_history и
    # никогда не идёт в PF. Если юзер правит закрытый месяц задним числом
    # (бывает: проводки, корректировки) — без удаления snapshot новые данные
    # никогда не появятся в дашборде. После удаления первый же /api/pnl
    # за этот месяц пересоберёт snapshot через cache_mode="save".
    snapshot_invalidated = await store.delete_cache_entry(
        session, user.planfact_key_id, period,
    )
    if snapshot_invalidated:
        await session.commit()

    # ── 3. Запуск фонового синка ops-метрик ──
    if is_ops_sync_running(user.planfact_key_id, period):
        return {
            "status": "already_running", "period": period,
            "snapshot_invalidated": snapshot_invalidated,
            "detail": "Синхронизация этого периода уже идёт.",
        }

    # Сразу помечаем что синк стартовал — на случай быстрых повторных кликов.
    # Реальный таск может дождаться чтобы create_task запустился (event loop).
    _OPS_SYNC_INFLIGHT.add((user.planfact_key_id, period))
    _asyncio.create_task(
        _run_ops_sync(
            user_id=user.id, period=period,
            inflight_key_id=user.planfact_key_id,
        )
    )
    return {
        "status": "scheduled", "period": period,
        "snapshot_invalidated": snapshot_invalidated,
    }


async def _run_ops_sync(
    *, user_id: int, period: str, inflight_key_id: int,
) -> None:
    """Фоновый воркер ops-синка. Создаёт собственную DB-сессию и httpx-клиента
    (нельзя переиспользовать запросные — они закрыты к моменту старта таска)."""
    import asyncio as _asyncio
    import logging
    import time
    from datetime import datetime
    from .auth.tokens import NoTokenError
    from .db import get_session_factory

    log = logging.getLogger("uvicorn.error")
    Sm = get_session_factory()

    async with Sm() as session:
        user = await session.get(User, user_id)
        if user is None or not user.planfact_key_id:
            log.warning("ops sync: user %s vanished or has no PF key", user_id)
            # B7: discard ровно тем кортежем, которым добавляли — раньше
            # при user=None или сменившемся ключе запись зависала навсегда
            # и блокировала повторные синки.
            _OPS_SYNC_INFLIGHT.discard((inflight_key_id, period))
            return

        pf_key_id = user.planfact_key_id
        try:
            y, m = map(int, period.split("-"))
            from_dt = datetime(y, m, 1, 0, 0, 0)
            to_dt = (
                datetime(y + 1, 1, 1, 0, 0, 0) if m == 12
                else datetime(y, m + 1, 1, 0, 0, 0)
            )

            cfg = await store.list_projects_config(session, pf_key_id)
            targets = [
                (pid, c["dodo_unit_uuid"])
                for pid, c in cfg.items() if c.get("dodo_unit_uuid")
            ]
            if not targets:
                log.info("ops sync %s: no units linked, ничего не делаем", period)
                return

            unit_uuids = [uuid for _, uuid in targets]

            async def _timed(name, coro):
                t = time.monotonic()
                try:
                    res = await coro
                    log.info("ops sync %s: %s done in %.1fs",
                             period, name, time.monotonic() - t)
                    return res
                except Exception as e:
                    log.warning("ops sync %s: %s FAILED in %.1fs: %s",
                                period, name, time.monotonic() - t, e)
                    raise

            t0 = time.monotonic()
            try:
                stats, cert_counts, delivery_stats, handover_rest = await _asyncio.gather(
                    _timed("productivity", with_dodois_retry(
                        session, user,
                        dodois_client.fetch_productivity_many,
                        unit_uuids, from_dt, to_dt,
                    )),
                    _timed("vouchers", with_dodois_retry(
                        session, user,
                        dodois_client.fetch_late_delivery_vouchers_count,
                        unit_uuids, from_dt, to_dt,
                    )),
                    _timed("delivery-stats", with_dodois_retry(
                        session, user,
                        dodois_client.fetch_delivery_statistics,
                        unit_uuids, from_dt, to_dt,
                    )),
                    # S16.1: restaurant cooking time — отдельной ручкой,
                    # фильтр salesChannels=DineIn (без дефиса! проверено
                    # эмпирически: 'Dine-in'/'Restaurant' возвращают 400,
                    # 'DineIn' и 'Delivery' — рабочие значения).
                    _timed("handover-restaurant", with_dodois_retry(
                        session, user,
                        dodois_client.fetch_orders_handover_statistics,
                        unit_uuids, from_dt, to_dt, sales_channels="DineIn",
                    )),
                )
            except (DodoISError, NoTokenError) as e:
                log.warning("ops sync %s: ABORT: %s", period, e)
                return

            # S16.3: incentives для KC_LIVE — ОТДЕЛЬНЫМ блоком, чтобы 403
            # (Insufficient Scopes) или другая ошибка не валила весь sync.
            # При недоступности — пропускаем расчёт KC, остальные плитки идут.
            incentives: list[dict] = []
            try:
                incentives = await _timed("incentives", with_dodois_retry(
                    session, user,
                    dodois_client.fetch_incentives_by_members,
                    unit_uuids, from_dt, to_dt,
                ))
            except (DodoISError, NoTokenError) as e:
                log.warning(
                    "ops sync %s: incentives FAILED (KC_LIVE will be null): %s",
                    period, e,
                )

            log.info(
                "ops sync %s: TOTAL %.1fs — units=%d, productivity=%d/certs=%d/delivery=%d/handover-rest=%d/incentives=%d",
                period, time.monotonic() - t0, len(unit_uuids),
                len(stats), len(cert_counts), len(delivery_stats),
                len(handover_rest), len(incentives),
            )

            by_uuid = {s.get("unitId", "").lower(): s for s in stats}
            by_uuid_dlv = {d.get("unitId", "").lower(): d for d in delivery_stats}
            by_uuid_hov = {h.get("unitId", "").lower(): h for h in handover_rest}

            # S16.3: KC_LIVE = sum(totalWage где staffType != 'Courier') / sales.
            # Пользователь решил: KC по факту = все смены кроме курьерских.
            # Управляющий в Dodo IS не платится (это отдельный оклад через PF),
            # так что KC_LIVE будет ≈ kitchen + cashiers без управляющего и
            # без налогов. Это «факт по сменам».
            kc_wage_by_unit: dict[str, float] = {}
            kc_staff_by_unit: dict[str, set[str]] = {}
            COURIER_TYPE = "Courier"
            for sm in incentives:
                staff_id = sm.get("staffId") or ""
                for sh in sm.get("shiftsDetailing") or []:
                    if (sh.get("staffType") or "") == COURIER_TYPE:
                        continue
                    uid_raw = (sh.get("unitId") or "").lower().replace("-", "")
                    if not uid_raw:
                        continue
                    wage = float(sh.get("totalWage") or 0)
                    kc_wage_by_unit[uid_raw] = kc_wage_by_unit.get(uid_raw, 0.0) + wage
                    kc_staff_by_unit.setdefault(uid_raw, set()).add(staff_id)
                # Premiums (вне-сменные) учитываем тем сотрудникам, у которых
                # были не-курьерские смены в том же юните.
                for pr in sm.get("premiums") or []:
                    uid_raw = (pr.get("unitId") or "").lower().replace("-", "")
                    if not uid_raw:
                        continue
                    if staff_id in kc_staff_by_unit.get(uid_raw, set()):
                        amount = float(pr.get("amount") or 0)
                        kc_wage_by_unit[uid_raw] = kc_wage_by_unit.get(uid_raw, 0.0) + amount
            for pid, uuid in targets:
                key = (uuid or "").lower().replace("-", "")
                s = by_uuid.get(key) or by_uuid.get(uuid.lower())
                d = by_uuid_dlv.get(key) or by_uuid_dlv.get(uuid.lower()) or {}
                h = by_uuid_hov.get(key) or by_uuid_hov.get(uuid.lower()) or {}
                cert_n = cert_counts.get(key, 0)
                delivery_orders = int(d.get("deliveryOrdersCount") or 0)
                cert_pct = (
                    (cert_n / delivery_orders * 100.0)
                    if delivery_orders > 0 else None
                )
                # S16: метрики из /delivery/statistics — все приходят в одном
                # ответе. Защищаемся от деления на ноль (новая точка без
                # запусков курьеров).
                trips_count = int(d.get("tripsCount") or 0)
                trips_duration = int(d.get("tripsDuration") or 0)
                couriers_shifts = int(d.get("couriersShiftsDuration") or 0)
                # S16.2: время хранится в секундах (INT), на UI mm:ss
                avg_trip_sec = d.get("avgOrderTripTime")
                avg_cook_delivery_sec = d.get("avgCookingTime")
                # S16.1: ресторанное время готовки — из отдельного запроса
                # /production/orders-handover-statistics?salesChannels=DineIn
                avg_cook_restaurant_sec = h.get("avgCookingTime")

                orders_per_trip = (
                    delivery_orders / trips_count if trips_count > 0 else None
                )
                courier_util_pct = (
                    trips_duration / couriers_shifts * 100.0
                    if couriers_shifts > 0 else None
                )

                # S16.3: KC_LIVE = (kitchen_wage / sales) × 100
                # sales берём из productivity.sales за тот же период.
                # Если нет ни одной кухонной смены ИЛИ нет выручки — None.
                kitchen_wage = kc_wage_by_unit.get(key, 0.0)
                sales_total = float(s.get("sales") or 0) if s else 0.0
                kc_live_pct = (
                    kitchen_wage / sales_total * 100.0
                    if sales_total > 0 and kitchen_wage > 0 else None
                )

                if not s:
                    continue
                await store.upsert_ops_metric(
                    session, pf_key_id, pid, period,
                    orders_per_courier_h=s.get("ordersPerCourierLabourHour"),
                    products_per_h=s.get("productsPerLaborHour"),
                    revenue_per_person_h=s.get("salesPerLaborHour"),
                    late_delivery_certs=cert_n,
                    delivery_orders_count=delivery_orders,
                    late_delivery_certs_pct=cert_pct,
                    orders_per_trip=orders_per_trip,
                    courier_utilization_pct=courier_util_pct,
                    avg_order_trip_time_sec=avg_trip_sec,
                    avg_cooking_time_delivery_sec=avg_cook_delivery_sec,
                    avg_cooking_time_restaurant_sec=avg_cook_restaurant_sec,
                    kc_live_pct=kc_live_pct,
                )
            await session.commit()
            log.info("ops sync %s: committed", period)
        except Exception:
            log.exception("ops sync %s: unhandled error", period)
        finally:
            # B7: добавляли (inflight_key_id, period) — его и убираем.
            # pf_key_id страхует случай смены ключа между schedule и run.
            _OPS_SYNC_INFLIGHT.discard((inflight_key_id, period))
            _OPS_SYNC_INFLIGHT.discard((pf_key_id, period))


# --- PnL template (импорт из экспорта ПланФакт) ---
# Шаблон привязан к planfact_key (см. модель PnLTemplateNode). Read —
# любой юзер с привязанным ключом, write — admin only. Точечный override
# (раньше был в category_mapping) теперь живёт через PATCH /api/template/{id}.

@app.get("/api/template")
async def get_template(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает текущий шаблон. nodes=[] означает, что шаблон не задан
    либо у юзера нет ключа PlanFact."""
    if not user.planfact_key_id:
        return {"nodes": [], "no_planfact_key": True}
    return {"nodes": await store.list_template_nodes(session, user.planfact_key_id)}


@app.post("/api/template/preview")
async def template_preview(
    file: UploadFile = File(...), user: User = Depends(require_admin)
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Ожидается .xlsx-файл (экспорт из ПланФакт).")
    # M1 (security-audit 2026-06-13): кап размера — экспорт ОПУ из ПланФакт
    # сильно меньше 10 МБ; читаем не больше лимита, чтобы недоверенный файл
    # (или zip-bomb через openpyxl) не выжрал память.
    _MAX_UPLOAD = 10 * 1024 * 1024
    content = await file.read(_MAX_UPLOAD + 1)
    if len(content) > _MAX_UPLOAD:
        raise HTTPException(413, "Файл слишком большой (макс. 10 МБ).")
    try:
        parsed = parse_pnl_export(content)
    except ExportParseError as e:
        raise HTTPException(400, str(e))
    return parsed


@app.put("/api/template")
async def save_template(
    payload: TemplateSaveIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Полная замена шаблона для ключа PlanFact юзера. Только админ —
    шаблон общий для всех юзеров с этим ключом."""
    pf_key_id = _require_user_pf_key(user)
    nodes = payload.nodes or []
    if not nodes:
        raise HTTPException(400, "Список узлов пуст — нечего сохранять.")
    for n in nodes:
        if "title" not in n or "depth" not in n or "path" not in n:
            raise HTTPException(400, "Узлы должны содержать title/depth/path.")
    inserted = await store.replace_template_tree(session, pf_key_id, nodes)
    return {"status": "ok", "inserted": inserted}


@app.patch("/api/template/{node_id}")
async def patch_template_node(
    node_id: int, payload: TemplateNodeCodeIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    ok = await store.update_template_node_code(
        session, pf_key_id, node_id, payload.pnl_code
    )
    if not ok:
        raise HTTPException(404, f"Узел {node_id} не найден.")
    return {"status": "ok"}


@app.delete("/api/template")
async def delete_template(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.clear_template(session, pf_key_id)
    return {"status": "ok"}


# --- P&L metrics (KPI с формулами) ---
# Привязаны к planfact_key (см. модель PnLMetric). Read — любой юзер с ключом,
# write — admin only. Формулы валидируются перед сохранением.

@app.get("/api/metrics")
async def list_metrics(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Список метрик ключа юзера. Если ключа нет — пусто + флаг."""
    if not user.planfact_key_id:
        return {"metrics": [], "no_planfact_key": True}
    return {"metrics": await store.list_metrics(session, user.planfact_key_id)}


@app.put("/api/metrics/{code}")
async def upsert_metric(
    code: str, payload: schemas.MetricIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    if payload.code != code:
        raise HTTPException(400, "code в URL и теле не совпадают")
    if payload.format not in ("pct", "rub", "x"):
        raise HTTPException(400, "format должен быть pct | rub | x")
    # Валидация формулы — парсим + проверяем что все [N] существуют в шаблоне.
    try:
        valid_lines = await store.template_line_nos(session, pf_key_id)
        formulas.parse_and_validate(payload.formula, valid_lines)
    except formulas.FormulaError as e:
        raise HTTPException(400, f"Формула: {e}")
    await store.upsert_metric(
        session, pf_key_id,
        code=payload.code,
        label=payload.label,
        formula=payload.formula,
        is_target=payload.is_target,
        format=payload.format,
        sort_order=payload.sort_order,
        min_visibility_level=payload.min_visibility_level,
        is_visible=payload.is_visible,
    )
    return {"status": "ok"}


@app.delete("/api/metrics/{code}")
async def delete_metric(
    code: str,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    pf_key_id = _require_user_pf_key(user)
    await store.delete_metric(session, pf_key_id, code)
    return {"status": "ok"}


@app.get("/api/board-metrics")
async def list_board_metrics(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Список ops-метрик rich-card на /board с флагами видимости.
    Источник списка — константа `board_module.BOARD_OPS_METRICS`,
    флаги — таблица board_card_metric_visibility per PF-ключ."""
    if not user.planfact_key_id:
        return {"metrics": [], "no_planfact_key": True}
    vis_map = await store.get_board_metrics_visibility(
        session, user.planfact_key_id,
    )
    metrics = []
    for m in board_module.BOARD_OPS_METRICS:
        # default visible если записи нет
        metrics.append({
            "code": m["code"],
            "group": m["group"],
            "label": m["label"],
            "is_visible": vis_map.get(m["code"], True),
        })
    return {"metrics": metrics}


@app.put("/api/board-metrics/{code}")
async def upsert_board_metric_visibility(
    code: str, payload: dict,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Обновить флаг видимости одной board-метрики. Принимает {is_visible: bool}."""
    pf_key_id = _require_user_pf_key(user)
    if code not in board_module.BOARD_OPS_METRIC_CODES:
        raise HTTPException(400, f"Неизвестный code: {code}")
    is_visible = bool(payload.get("is_visible", True))
    await store.upsert_board_metric_visibility(
        session, pf_key_id, code, is_visible,
    )
    return {"status": "ok"}


@app.post("/api/metrics/preview")
async def preview_metric(
    payload: schemas.FormulaPreviewIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Распарсить формулу + валидировать ссылки. Возвращаем список line_refs
    с метками строк шаблона — UI показывает «формула ссылается на: [13]
    Себестоимость, [7] Выручка, ...»."""
    pf_key_id = _require_user_pf_key(user)
    try:
        node = formulas.parse(payload.formula)
    except formulas.FormulaError as e:
        return {"ok": False, "error": str(e), "line_refs": []}
    refs = sorted(formulas.line_refs(node))
    # Подтянем title для каждой существующей строки
    nodes = await store.list_template_nodes(session, pf_key_id)
    line_to_title = {n["line_no"]: n["title"] for n in nodes}
    refs_info = [
        {
            "line_no": ln,
            "title": line_to_title.get(ln),
            "exists": ln in line_to_title,
        }
        for ln in refs
    ]
    missing = [r["line_no"] for r in refs_info if not r["exists"]]
    error_msg = None
    if missing:
        error_msg = "Несуществующие строки: " + ", ".join(f"[{ln}]" for ln in missing)
    return {
        "ok": not missing,
        "error": error_msg,
        "line_refs": refs_info,
    }


# --- helpers ---

async def _fetch_period(
    pf: PlanFactClient,
    date_start: str,
    date_end: str,
    project_ids: list[str] | None,
    *,
    method: str = "accrual",
):
    """Параллельно тянем проекты, категории и операции за период через
    инстанс клиента, привязанный к ключу текущего пользователя."""
    import asyncio
    projects, categories, operations = await asyncio.gather(
        pf.list_projects(),
        pf.list_operation_categories(),
        pf.fetch_all_operations(
            date_start=date_start,
            date_end=date_end,
            project_ids=project_ids,
            method=method,
        ),
    )
    return projects, categories, operations
