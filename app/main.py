"""FastAPI-приложение: дашборд + API.

Multi-tenant: каждый запрос несёт user (из require_user dependency) и session
(из get_session). Все обращения к данным идут через app.store.* с
обязательным owner_id=user.id.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from . import dodois_client
from . import pnl as pnl_module
from . import store
from .auth.admin_router import admin_router
from .auth.dependencies import optional_user, require_admin, require_user
from .auth.router import router as auth_router
from .auth.models import User
from .auth.tokens import NoTokenError, get_dodois_token, get_planfact_key
from .config import settings
from .db import get_session
from .dodois_client import DodoISError
from .planfact import PlanFactClient, PlanFactError, get_planfact_client, invalidate_planfact_for
from .planfact_export import ExportParseError, parse_pnl_export
from .schemas import (
    DefaultTargetIn,
    MappingIn,
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
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


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


# --- API routes ---

@app.get("/api/health")
async def health():
    return {"status": "ok", "planfact_key_set": bool(settings.planfact_api_key)}


@app.get("/api/projects")
async def get_projects(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
):
    pf = await planfact_for(session, user)
    try:
        projects = await pf.list_projects()
    except PlanFactError as e:
        raise HTTPException(502, str(e))
    cfg = await store.list_projects_config(session, user.id)
    norm = []
    for p in projects:
        pid = str(p.get("projectId") or p.get("id") or "")
        if not pid:
            continue
        c = cfg.get(pid) or {}
        norm.append({
            "id": pid,
            "planfact_name": p.get("title") or p.get("name") or "",
            "name": c.get("display_name") or p.get("title") or p.get("name") or "",
            "display_name": c.get("display_name"),
            "is_active": bool(c.get("is_active", True)),
            "sort_order": c.get("sort_order"),
            "planfact_active": bool(p.get("active", True)),
            "dodo_unit_uuid": c.get("dodo_unit_uuid"),
        })
    return {"projects": norm}


@app.get("/api/categories")
async def get_categories(
    user: User = Depends(require_user), session: AsyncSession = Depends(get_session)
):
    pf = await planfact_for(session, user)
    try:
        cats = await pf.list_operation_categories()
    except PlanFactError as e:
        raise HTTPException(502, str(e))
    index = await pnl_module._build_category_index(session, user.id, cats)
    out = []
    for cid, info in index.items():
        out.append({
            "id": cid,
            "title": info["title"],
            "path": info["path"],
            "op_type": info["op_type"],
            "activity_type": info["activity_type"],
            "pnl_code": info["pnl_code"],
        })
    return {"categories": out, "mappings": await store.list_mappings(session, user.id)}


def _derive_period_month(date_start: str, date_end: str) -> str | None:
    if not date_start or not date_end or len(date_start) < 7 or len(date_end) < 7:
        return None
    if date_start[:7] == date_end[:7]:
        return date_start[:7]
    return None


async def _resolve_project_filter(
    session: AsyncSession, owner_id: int, project_ids: list[str] | None
) -> list[str] | None:
    if project_ids:
        return project_ids
    active = await store.get_active_project_ids(session, owner_id)
    if active is None:
        return None
    return sorted(active) if active else []


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
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    effective_projects = await _resolve_project_filter(session, user.id, project_ids)
    if effective_projects is not None and len(effective_projects) == 0:
        return {
            "projects": [], "lines": [], "template_lines": [], "targets": [],
            "category_breakdown": [], "revenue_by_channel": {}, "unclassified": [],
            "pnl_codes": pnl_module.PNL_CODES,
            "targetable_metrics": pnl_module.TARGETABLE_METRICS,
            "computed_targetable_metrics": sorted(pnl_module.COMPUTED_TARGETABLE_METRICS),
            "denominators": pnl_module.DENOMINATOR,
            "method": method, "period_month": period_month, "stats": {},
            "settings": {
                "include_manager_in_lc": await store.get_bool_setting(
                    session, user.id, "include_manager_in_lc", True
                )
            },
            "default_targets": await store.list_default_targets(session, user.id),
            "ops_targets": await store.list_ops_targets(session, user.id),
            "ops_metrics_meta": store.OPS_METRICS,
            "period": {"current": {"start": date_start, "end": date_end}},
        }

    pm = period_month or _derive_period_month(date_start, date_end)

    pf = await planfact_for(session, user)
    try:
        projects, categories, operations = await _fetch_period(
            pf, date_start, date_end, effective_projects, method=method,
        )
        result = await pnl_module.build_pnl(
            session=session, owner_id=user.id,
            categories=categories, operations=operations, projects=projects,
            project_filter=effective_projects,
            date_start=date_start, date_end=date_end,
            method=method, period_month=pm,
        )
        if compare_start and compare_end:
            prev_operations = await pf.fetch_all_operations(
                date_start=compare_start, date_end=compare_end,
                project_ids=effective_projects, method=method,
            )
            prev = await pnl_module.build_pnl(
                session=session, owner_id=user.id,
                categories=categories, operations=prev_operations, projects=projects,
                project_filter=effective_projects,
                date_start=compare_start, date_end=compare_end,
                method=method,
                period_month=_derive_period_month(compare_start, compare_end),
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
    return result


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
    effective_projects = await _resolve_project_filter(session, user.id, project_ids)
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
        _, categories, operations = await _fetch_period(
            pf, date_start, date_end, effective_projects, method=method,
        )
        cur = pnl_module.build_revenue_history(
            categories=categories,
            operations=operations,
            project_filter=effective_projects,
            months=period_months,
            method=method,
        )

        out: dict = {
            "months": cur["months"],
            "totals": cur["totals"],
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
            ly_operations = await pf.fetch_all_operations(
                date_start=ly_start,
                date_end=ly_end,
                project_ids=effective_projects,
                method=method,
            )
            ly = pnl_module.build_revenue_history(
                categories=categories,
                operations=ly_operations,
                project_filter=effective_projects,
                months=ly_months,
                method=method,
            )
            out["ly"] = {
                "months": ly["months"],
                "totals": ly["totals"],
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

    try:
        data = await pf.list_operations(
            date_start=date_start,
            date_end=date_end,
            project_ids=[project_id] if project_id else None,
            category_ids=cat_ids_list,
            offset=offset,
            limit=limit,
        )
    except PlanFactError as e:
        raise HTTPException(502, str(e))

    # Нормализуем операции: фильтруем operationParts по project_id и category_ids.
    items = data.get("items") or []
    norm = []
    sum_value = 0.0
    for op in items:
        parts = op.get("operationParts") or []
        if project_id:
            parts = [p for p in parts if str((p.get("project") or {}).get("projectId")) == project_id]
        if cat_ids_list:
            cat_ids_set = set(cat_ids_list)
            parts = [
                p for p in parts
                if str((p.get("operationCategory") or {}).get("operationCategoryId")) in cat_ids_set
            ]
        op_type = op.get("operationType") or ""
        # Знак для суммы: Outcome — со знаком минус, всё остальное — как есть.
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
    return {
        "items": norm,
        "total": data.get("total"),
        "raw_count": len(items),
        "filtered_count": len(norm),
        "sum_value": sum_value,
    }


@app.post("/api/refresh")
async def refresh_cache(user: User = Depends(require_user)):
    invalidate_planfact_for(user.id)
    return {"status": "ok"}


# --- Targets CRUD ---

@app.get("/api/targets")
async def list_targets(
    project_id: str | None = None,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    return {"targets": await store.list_targets(session, user.id, project_id)}


@app.post("/api/targets")
async def upsert_target(
    payload: TargetIn,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.upsert_target(
        session, user.id, payload.project_id, payload.metric_code, payload.target_pct
    )
    return {"status": "ok"}


@app.delete("/api/targets")
async def delete_target(
    project_id: str, metric_code: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.delete_target(session, user.id, project_id, metric_code)
    return {"status": "ok"}


# --- Default targets (fallback для всех проектов) ---

@app.get("/api/targets/defaults")
async def list_default_targets(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    return {"defaults": await store.list_default_targets(session, user.id)}


@app.post("/api/targets/defaults")
async def upsert_default_target(
    payload: DefaultTargetIn,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.upsert_default_target(
        session, user.id, payload.metric_code, payload.target_pct
    )
    return {"status": "ok"}


@app.delete("/api/targets/defaults")
async def delete_default_target(
    metric_code: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.delete_default_target(session, user.id, metric_code)
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
    return {"config": await store.list_projects_config(session, user.id)}


@app.post("/api/projects/config")
async def upsert_projects_config(
    payload: ProjectConfigIn,
    # Только администратор может архивировать проекты, переименовывать и
    # подвязывать Dodo IS unit. Обычный юзер видит итог через GET и пользуется
    # сайдбар-чекбоксами как сессионным фильтром.
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    kwargs: dict = {
        "is_active": payload.is_active,
        "display_name": payload.display_name,
        "sort_order": payload.sort_order,
    }
    if "dodo_unit_uuid" in payload.model_fields_set:
        kwargs["dodo_unit_uuid"] = payload.dodo_unit_uuid
    await store.upsert_project_config(session, user.id, payload.project_id, **kwargs)
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
    return {
        "metrics": await store.list_ops_metrics(
            session, user.id, period_month=period_month, project_id=project_id
        ),
        "meta": store.OPS_METRICS,
        "targets": await store.list_ops_targets(session, user.id),
    }


@app.post("/api/ops-metrics")
async def upsert_ops_metric(
    payload: OpsMetricIn,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.upsert_ops_metric(
        session, user.id, payload.project_id, payload.period_month,
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
    await store.delete_ops_metric(session, user.id, project_id, period_month)
    return {"status": "ok"}


# --- Ops targets (глобальные цели по ops-метрикам) ---

@app.get("/api/ops-targets")
async def list_ops_targets_ep(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    return {
        "targets": await store.list_ops_targets(session, user.id),
        "project_targets": await store.list_ops_project_targets(session, user.id),
        "meta": store.OPS_METRICS,
    }


@app.post("/api/ops-targets")
async def upsert_ops_target_ep(
    payload: OpsTargetIn,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.upsert_ops_target(session, user.id, payload.metric_code, payload.target_value)
    return {"status": "ok"}


@app.delete("/api/ops-targets")
async def delete_ops_target_ep(
    metric_code: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.delete_ops_target(session, user.id, metric_code)
    return {"status": "ok"}


@app.post("/api/ops-targets/project")
async def upsert_ops_project_target_ep(
    payload: OpsProjectTargetIn,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.upsert_ops_project_target(
        session, user.id, payload.project_id, payload.metric_code, payload.target_value
    )
    return {"status": "ok"}


@app.delete("/api/ops-targets/project")
async def delete_ops_project_target_ep(
    project_id: str, metric_code: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.delete_ops_project_target(session, user.id, project_id, metric_code)
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


@app.post("/api/ops-metrics/sync")
async def sync_ops_metrics_from_dodois(
    period: str = Query(..., description="'YYYY-MM' — месяц, для которого тянем ops"),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Тянет ops-метрики из Dodo IS для всех проектов текущего юзера, у которых
    задан dodo_unit_uuid. Идемпотентно UPSERT-ит в ops_metrics."""
    from datetime import datetime

    try:
        y, m = map(int, period.split("-"))
        from_dt = datetime(y, m, 1, 0, 0, 0)
        if m == 12:
            to_dt = datetime(y + 1, 1, 1, 0, 0, 0)
        else:
            to_dt = datetime(y, m + 1, 1, 0, 0, 0)
    except Exception:
        raise HTTPException(400, "period должен быть 'YYYY-MM'")

    cfg = await store.list_projects_config(session, user.id)
    targets = [
        (pid, c["dodo_unit_uuid"])
        for pid, c in cfg.items()
        if c.get("dodo_unit_uuid")
    ]
    if not targets:
        return {"status": "ok", "updated": [], "skipped_no_uuid": list(cfg)}

    unit_uuids = [uuid for _, uuid in targets]
    try:
        stats = await with_dodois_retry(
            session, user,
            dodois_client.fetch_productivity_many, unit_uuids, from_dt, to_dt,
        )
        cert_counts = await with_dodois_retry(
            session, user,
            dodois_client.fetch_late_delivery_vouchers_count, unit_uuids, from_dt, to_dt,
        )
        delivery_stats = await with_dodois_retry(
            session, user,
            dodois_client.fetch_delivery_statistics, unit_uuids, from_dt, to_dt,
        )
    except DodoISError as e:
        raise HTTPException(502, str(e))

    by_uuid = {s.get("unitId", "").lower(): s for s in stats}
    by_uuid_dlv = {d.get("unitId", "").lower(): d for d in delivery_stats}

    updated: list[dict] = []
    not_found: list[str] = []
    for pid, uuid in targets:
        key = (uuid or "").lower().replace("-", "")
        s = by_uuid.get(key) or by_uuid.get(uuid.lower())
        d = by_uuid_dlv.get(key) or by_uuid_dlv.get(uuid.lower()) or {}
        cert_n = cert_counts.get(key, 0)
        delivery_orders = int(d.get("deliveryOrdersCount") or 0)
        cert_pct = (cert_n / delivery_orders * 100.0) if delivery_orders > 0 else None
        if not s:
            not_found.append(pid)
            continue
        await store.upsert_ops_metric(
            session, user.id, pid, period,
            orders_per_courier_h=s.get("ordersPerCourierLabourHour"),
            products_per_h=s.get("productsPerLaborHour"),
            revenue_per_person_h=s.get("salesPerLaborHour"),
            late_delivery_certs=cert_n,
            delivery_orders_count=delivery_orders,
            late_delivery_certs_pct=cert_pct,
        )
        updated.append({
            "project_id": pid,
            "unit_name": s.get("unitName"),
            "orders_per_courier_h": s.get("ordersPerCourierLabourHour"),
            "products_per_h": s.get("productsPerLaborHour"),
            "revenue_per_person_h": s.get("salesPerLaborHour"),
            "late_delivery_certs": cert_n,
            "delivery_orders_count": delivery_orders,
            "late_delivery_certs_pct": cert_pct,
        })
    return {"status": "ok", "period": period, "updated": updated,
            "not_found_in_response": not_found}


# --- Category mapping ---

@app.post("/api/mappings")
async def upsert_mapping(
    payload: MappingIn,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.upsert_mapping(
        session, user.id, payload.planfact_category_id, payload.pnl_code
    )
    return {"status": "ok"}


# --- PnL template (импорт из экспорта ПланФакт) ---

@app.get("/api/template")
async def get_template(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """Возвращает текущий шаблон. nodes=[] означает, что шаблон не задан."""
    return {"nodes": await store.list_template_nodes(session, user.id)}


@app.post("/api/template/preview")
async def template_preview(
    file: UploadFile = File(...), user: User = Depends(require_user)
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Ожидается .xlsx-файл (экспорт из ПланФакт).")
    content = await file.read()
    try:
        parsed = parse_pnl_export(content)
    except ExportParseError as e:
        raise HTTPException(400, str(e))
    return parsed


@app.put("/api/template")
async def save_template(
    payload: TemplateSaveIn,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    nodes = payload.nodes or []
    if not nodes:
        raise HTTPException(400, "Список узлов пуст — нечего сохранять.")
    for n in nodes:
        if "title" not in n or "depth" not in n or "path" not in n:
            raise HTTPException(400, "Узлы должны содержать title/depth/path.")
    inserted = await store.replace_template_tree(session, user.id, nodes)
    return {"status": "ok", "inserted": inserted}


@app.patch("/api/template/{node_id}")
async def patch_template_node(
    node_id: int, payload: TemplateNodeCodeIn,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    ok = await store.update_template_node_code(
        session, user.id, node_id, payload.pnl_code
    )
    if not ok:
        raise HTTPException(404, f"Узел {node_id} не найден.")
    return {"status": "ok"}


@app.delete("/api/template")
async def delete_template(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    await store.clear_template(session, user.id)
    return {"status": "ok"}


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
