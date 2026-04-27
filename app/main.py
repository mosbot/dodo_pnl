"""FastAPI-приложение: дашборд + API."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import dodois_client
from . import pnl as pnl_module
from . import storage
from .auth.dependencies import optional_user, require_user
from .auth.router import router as auth_router
from .auth.models import User
from .config import settings
from .dodois_client import DodoISError
from .planfact import PlanFactError, client
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SQLite-init для legacy single-tenant store. Будет вырезано в S2.
    storage.init_db()
    yield


app = FastAPI(title="PnL Dashboard", lifespan=lifespan)

# --- auth ---
# Подключаем /auth/login, /auth/logout, /auth/me
app.include_router(auth_router)


def _auth_dep():
    """Совместимость: все существующие @app.get(..., dependencies=[Depends(_auth_dep())])
    теперь требуют валидную сессию pnl_session. 401 без неё — для /api/* это
    JSON-ошибка, фронт обрабатывает её редиректом на /login."""
    return require_user


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


@app.get("/api/projects", dependencies=[Depends(_auth_dep())])
async def get_projects():
    try:
        projects = await client.list_projects()
    except PlanFactError as e:
        raise HTTPException(502, str(e))
    cfg = storage.list_projects_config()
    # Нормализуем форму для фронта. is_active=True по умолчанию — пока нет записи
    # в projects_config, считаем проект активным.
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


@app.get("/api/categories", dependencies=[Depends(_auth_dep())])
async def get_categories():
    try:
        cats = await client.list_operation_categories()
    except PlanFactError as e:
        raise HTTPException(502, str(e))
    # Дадим фронту список с path + автоклассификацией, чтобы можно было править
    index = pnl_module._build_category_index(cats)
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
    return {"categories": out, "mappings": storage.list_mappings()}


def _derive_period_month(date_start: str, date_end: str) -> str | None:
    """Если диапазон лежит ровно в одном календарном месяце — вернуть 'YYYY-MM'.
    Иначе None (тогда ops-метрики на карточке не показываем)."""
    if not date_start or not date_end or len(date_start) < 7 or len(date_end) < 7:
        return None
    if date_start[:7] == date_end[:7]:
        return date_start[:7]
    return None


def _resolve_project_filter(project_ids: list[str] | None) -> list[str] | None:
    """Если пользователь не прислал явный список — берём активные из projects_config.
    Если в projects_config пусто — возвращаем None (= все)."""
    if project_ids:
        return project_ids
    active = storage.get_active_project_ids()
    if active is None:
        return None
    return sorted(active) if active else []


@app.get("/api/pnl", dependencies=[Depends(_auth_dep())])
async def get_pnl(
    date_start: str = Query(..., description="YYYY-MM-DD"),
    date_end: str = Query(..., description="YYYY-MM-DD"),
    project_ids: list[str] | None = Query(None),
    compare_start: str | None = Query(None),
    compare_end: str | None = Query(None),
    compare_mode: str = Query("lfl", regex="^(lfl|mom)$"),
    method: str = Query("accrual", regex="^(accrual|cash)$"),
    period_month: str | None = Query(None, description="'YYYY-MM'. Если не задан — выводится из дат."),
):
    # если фронт не прислал проекты — подставим активные из projects_config
    effective_projects = _resolve_project_filter(project_ids)
    if effective_projects is not None and len(effective_projects) == 0:
        # Есть конфиг, и ни одного активного проекта — отдаём пустой результат
        return {
            "projects": [],
            "lines": [],
            "template_lines": [],
            "targets": [],
            "category_breakdown": [],
            "revenue_by_channel": {},
            "unclassified": [],
            "pnl_codes": pnl_module.PNL_CODES,
            "targetable_metrics": pnl_module.TARGETABLE_METRICS,
            "computed_targetable_metrics": sorted(pnl_module.COMPUTED_TARGETABLE_METRICS),
            "denominators": pnl_module.DENOMINATOR,
            "method": method,
            "period_month": period_month,
            "stats": {},
            "settings": {"include_manager_in_lc": storage.get_bool_setting("include_manager_in_lc", True)},
            "default_targets": storage.list_default_targets(),
            "ops_targets": storage.list_ops_targets(),
            "ops_metrics_meta": storage.OPS_METRICS,
            "period": {"current": {"start": date_start, "end": date_end}},
        }

    pm = period_month or _derive_period_month(date_start, date_end)

    try:
        projects, categories, operations = await _fetch_period(
            date_start, date_end, effective_projects, method=method,
        )
        result = pnl_module.build_pnl(
            categories=categories,
            operations=operations,
            projects=projects,
            project_filter=effective_projects,
            date_start=date_start,
            date_end=date_end,
            method=method,
            period_month=pm,
        )
        if compare_start and compare_end:
            prev_operations = await client.fetch_all_operations(
                date_start=compare_start,
                date_end=compare_end,
                project_ids=effective_projects,
                method=method,
            )
            prev = pnl_module.build_pnl(
                categories=categories,
                operations=prev_operations,
                projects=projects,
                project_filter=effective_projects,
                date_start=compare_start,
                date_end=compare_end,
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


@app.get("/api/revenue-history", dependencies=[Depends(_auth_dep())])
async def get_revenue_history(
    anchor: str = Query(..., description="'YYYY-MM' — последний месяц окна"),
    months: int = Query(12, ge=1, le=36),
    project_ids: list[str] | None = Query(None),
    include_ly: bool = Query(False),
    method: str = Query("accrual", regex="^(accrual|cash)$"),
):
    """Выручка по месяцам за окно [anchor-months+1 .. anchor], опционально + LFL (тот же месяц годом ранее)."""
    effective_projects = _resolve_project_filter(project_ids)
    if effective_projects is not None and len(effective_projects) == 0:
        return {"months": [], "totals": {}, "projects": {}, "project_names": {}}

    period_months = pnl_module.month_range(anchor, months)
    date_start = f"{period_months[0]}-01"
    last_y, last_m = (int(x) for x in period_months[-1].split("-"))
    from calendar import monthrange
    last_day = monthrange(last_y, last_m)[1]
    date_end = f"{period_months[-1]}-{last_day:02d}"

    try:
        _, categories, operations = await _fetch_period(
            date_start, date_end, effective_projects, method=method,
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
            ly_operations = await client.fetch_all_operations(
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


@app.get("/api/operations", dependencies=[Depends(_auth_dep())])
async def get_operations(
    date_start: str,
    date_end: str,
    project_id: str | None = None,
    category_id: str | None = None,
    category_ids: list[str] = Query(default_factory=list),
    offset: int = 0,
    limit: int = 100,
):
    # Объединяем legacy single category_id и новый список category_ids.
    cat_id_set: set[str] = set()
    if category_id:
        cat_id_set.add(category_id)
    for c in (category_ids or []):
        if c:
            cat_id_set.add(c)
    cat_ids_list: list[str] | None = sorted(cat_id_set) if cat_id_set else None

    try:
        data = await client.list_operations(
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


@app.post("/api/refresh", dependencies=[Depends(_auth_dep())])
async def refresh_cache():
    client.invalidate_cache()
    return {"status": "ok"}


# --- Targets CRUD ---

@app.get("/api/targets", dependencies=[Depends(_auth_dep())])
async def list_targets(project_id: str | None = None):
    return {"targets": storage.list_targets(project_id)}


@app.post("/api/targets", dependencies=[Depends(_auth_dep())])
async def upsert_target(payload: TargetIn):
    storage.upsert_target(payload.project_id, payload.metric_code, payload.target_pct)
    return {"status": "ok"}


@app.delete("/api/targets", dependencies=[Depends(_auth_dep())])
async def delete_target(project_id: str, metric_code: str):
    storage.delete_target(project_id, metric_code)
    return {"status": "ok"}


# --- Default targets (fallback для всех проектов) ---

@app.get("/api/targets/defaults", dependencies=[Depends(_auth_dep())])
async def list_default_targets():
    return {"defaults": storage.list_default_targets()}


@app.post("/api/targets/defaults", dependencies=[Depends(_auth_dep())])
async def upsert_default_target(payload: DefaultTargetIn):
    storage.upsert_default_target(payload.metric_code, payload.target_pct)
    return {"status": "ok"}


@app.delete("/api/targets/defaults", dependencies=[Depends(_auth_dep())])
async def delete_default_target(metric_code: str):
    storage.delete_default_target(metric_code)
    return {"status": "ok"}


# --- App settings ---

@app.get("/api/settings", dependencies=[Depends(_auth_dep())])
async def get_settings():
    return {"settings": storage.list_settings()}


@app.post("/api/settings", dependencies=[Depends(_auth_dep())])
async def set_settings(payload: SettingIn):
    storage.set_setting(payload.key, payload.value)
    client.invalidate_cache()  # расчёт зависит от настроек — сбросим кэш
    return {"status": "ok"}


# --- Projects config (активность / имя / сортировка) ---

@app.get("/api/projects/config", dependencies=[Depends(_auth_dep())])
async def get_projects_config():
    return {"config": storage.list_projects_config()}


@app.post("/api/projects/config", dependencies=[Depends(_auth_dep())])
async def upsert_projects_config(payload: ProjectConfigIn):
    # model_fields_set — только те поля, что реально были в JSON-запросе.
    # Это отличает «не менять» от «очистить».
    kwargs: dict = {
        "is_active": payload.is_active,
        "display_name": payload.display_name,
        "sort_order": payload.sort_order,
    }
    if "dodo_unit_uuid" in payload.model_fields_set:
        kwargs["dodo_unit_uuid"] = payload.dodo_unit_uuid
    storage.upsert_project_config(payload.project_id, **kwargs)
    client.invalidate_cache()
    return {"status": "ok"}


# --- Ops metrics (ручной ввод на /settings) ---

@app.get("/api/ops-metrics", dependencies=[Depends(_auth_dep())])
async def get_ops_metrics(
    period_month: str | None = Query(None, description="'YYYY-MM'. Если не задан — все месяцы."),
    project_id: str | None = None,
):
    return {
        "metrics": storage.list_ops_metrics(period_month=period_month, project_id=project_id),
        "meta": storage.OPS_METRICS,
        "targets": storage.list_ops_targets(),
    }


@app.post("/api/ops-metrics", dependencies=[Depends(_auth_dep())])
async def upsert_ops_metric(payload: OpsMetricIn):
    storage.upsert_ops_metric(
        payload.project_id,
        payload.period_month,
        orders_per_courier_h=payload.orders_per_courier_h,
        products_per_h=payload.products_per_h,
        revenue_per_person_h=payload.revenue_per_person_h,
    )
    return {"status": "ok"}


@app.delete("/api/ops-metrics", dependencies=[Depends(_auth_dep())])
async def delete_ops_metric(project_id: str, period_month: str):
    storage.delete_ops_metric(project_id, period_month)
    return {"status": "ok"}


# --- Ops targets (глобальные цели по ops-метрикам) ---

@app.get("/api/ops-targets", dependencies=[Depends(_auth_dep())])
async def list_ops_targets_ep():
    return {
        "targets": storage.list_ops_targets(),                    # global defaults
        "project_targets": storage.list_ops_project_targets(),    # per-project overrides
        "meta": storage.OPS_METRICS,
    }


@app.post("/api/ops-targets", dependencies=[Depends(_auth_dep())])
async def upsert_ops_target_ep(payload: OpsTargetIn):
    storage.upsert_ops_target(payload.metric_code, payload.target_value)
    return {"status": "ok"}


@app.delete("/api/ops-targets", dependencies=[Depends(_auth_dep())])
async def delete_ops_target_ep(metric_code: str):
    storage.delete_ops_target(metric_code)
    return {"status": "ok"}


@app.post("/api/ops-targets/project", dependencies=[Depends(_auth_dep())])
async def upsert_ops_project_target_ep(payload: OpsProjectTargetIn):
    storage.upsert_ops_project_target(
        payload.project_id, payload.metric_code, payload.target_value
    )
    return {"status": "ok"}


@app.delete("/api/ops-targets/project", dependencies=[Depends(_auth_dep())])
async def delete_ops_project_target_ep(project_id: str, metric_code: str):
    storage.delete_ops_project_target(project_id, metric_code)
    return {"status": "ok"}


# --- Dodo IS ---

@app.get("/api/dodois/units", dependencies=[Depends(_auth_dep())])
async def dodois_units():
    """Список юнитов пользователя из Dodo IS. Использует access_token из env."""
    try:
        units = await dodois_client.fetch_units()
    except DodoISError as e:
        raise HTTPException(502, str(e))
    # Фильтруем: unitType=1 — пиццерия/точка, 0 — офис (не нужен).
    pizzerias = [u for u in units if u.get("unitType") == 1]
    return {"units": pizzerias, "all": units}


@app.post("/api/ops-metrics/sync", dependencies=[Depends(_auth_dep())])
async def sync_ops_metrics_from_dodois(
    period: str = Query(..., description="'YYYY-MM' — месяц, для которого тянем ops"),
):
    """Для каждого проекта с dodo_unit_uuid дёргает /production/productivity
    и UPSERT-ит в ops_metrics. Возвращает отчёт: кто обновлён, кто пропущен."""
    from datetime import datetime

    try:
        y, m = map(int, period.split("-"))
        from_dt = datetime(y, m, 1, 0, 0, 0)
        # Dodo IS требует, чтобы границы были выровнены по часу — берём
        # начало следующего месяца (exclusive верхняя граница).
        if m == 12:
            to_dt = datetime(y + 1, 1, 1, 0, 0, 0)
        else:
            to_dt = datetime(y, m + 1, 1, 0, 0, 0)
    except Exception:
        raise HTTPException(400, "period должен быть 'YYYY-MM'")

    cfg = storage.list_projects_config()
    targets = [
        (pid, c["dodo_unit_uuid"])
        for pid, c in cfg.items()
        if c.get("dodo_unit_uuid")
    ]
    if not targets:
        return {"status": "ok", "updated": [], "skipped_no_uuid": list(cfg)}

    unit_uuids = [uuid for _, uuid in targets]
    try:
        stats = await dodois_client.fetch_productivity_many(
            unit_uuids, from_dt, to_dt
        )
        cert_counts = await dodois_client.fetch_late_delivery_vouchers_count(
            unit_uuids, from_dt, to_dt
        )
        delivery_stats = await dodois_client.fetch_delivery_statistics(
            unit_uuids, from_dt, to_dt
        )
    except DodoISError as e:
        raise HTTPException(502, str(e))

    by_uuid = {s.get("unitId", "").lower(): s for s in stats}
    # delivery stats ключуем тем же способом, что и productivity — по unitId lower-case
    by_uuid_dlv = {d.get("unitId", "").lower(): d for d in delivery_stats}

    updated: list[dict] = []
    not_found: list[str] = []
    for pid, uuid in targets:
        key = (uuid or "").lower().replace("-", "")
        s = by_uuid.get(key) or by_uuid.get(uuid.lower())
        d = by_uuid_dlv.get(key) or by_uuid_dlv.get(uuid.lower()) or {}
        # cert_counts ключуется нормализованным uuid (lower, без дефисов).
        cert_n = cert_counts.get(key, 0)
        delivery_orders = int(d.get("deliveryOrdersCount") or 0)
        cert_pct = (cert_n / delivery_orders * 100.0) if delivery_orders > 0 else None
        if not s:
            not_found.append(pid)
            continue
        storage.upsert_ops_metric(
            pid,
            period,
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

@app.post("/api/mappings", dependencies=[Depends(_auth_dep())])
async def upsert_mapping(payload: MappingIn):
    storage.upsert_mapping(payload.planfact_category_id, payload.pnl_code)
    return {"status": "ok"}


# --- PnL template (импорт из экспорта ПланФакт) ---

@app.get("/api/template", dependencies=[Depends(_auth_dep())])
async def get_template():
    """Возвращает текущий шаблон. nodes=[] означает, что шаблон не задан."""
    return {"nodes": storage.list_template_nodes()}


@app.post("/api/template/preview", dependencies=[Depends(_auth_dep())])
async def template_preview(file: UploadFile = File(...)):
    """Парсит загруженный xlsx-экспорт ПланФакт и возвращает дерево с
    auto-классификацией. На этом шаге в БД ничего не пишем."""
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Ожидается .xlsx-файл (экспорт из ПланФакт).")
    content = await file.read()
    try:
        parsed = parse_pnl_export(content)
    except ExportParseError as e:
        raise HTTPException(400, str(e))
    return parsed


@app.put("/api/template", dependencies=[Depends(_auth_dep())])
async def save_template(payload: TemplateSaveIn):
    """Сохранить шаблон целиком (полная замена). На входе — список узлов из preview."""
    nodes = payload.nodes or []
    if not nodes:
        raise HTTPException(400, "Список узлов пуст — нечего сохранять.")
    # Лёгкая валидация формы
    for n in nodes:
        if "title" not in n or "depth" not in n or "path" not in n:
            raise HTTPException(400, "Узлы должны содержать title/depth/path.")
    inserted = storage.replace_template_tree(nodes)
    return {"status": "ok", "inserted": inserted}


@app.patch("/api/template/{node_id}", dependencies=[Depends(_auth_dep())])
async def patch_template_node(node_id: int, payload: TemplateNodeCodeIn):
    """Поправить pnl_code конкретного узла (без полного пересохранения)."""
    ok = storage.update_template_node_code(node_id, payload.pnl_code)
    if not ok:
        raise HTTPException(404, f"Узел {node_id} не найден.")
    return {"status": "ok"}


@app.delete("/api/template", dependencies=[Depends(_auth_dep())])
async def delete_template():
    """Очистить шаблон (вернуться к классификации по эвристике)."""
    storage.clear_template()
    return {"status": "ok"}


# --- helpers ---

async def _fetch_period(
    date_start: str,
    date_end: str,
    project_ids: list[str] | None,
    *,
    method: str = "accrual",
):
    """Параллельно тянем проекты, категории и операции за период."""
    import asyncio
    projects, categories, operations = await asyncio.gather(
        client.list_projects(),
        client.list_operation_categories(),
        client.fetch_all_operations(
            date_start=date_start,
            date_end=date_end,
            project_ids=project_ids,
            method=method,
        ),
    )
    return projects, categories, operations
