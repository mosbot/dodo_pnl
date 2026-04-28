"""Async PG-store для всех multi-tenant данных pnl-service.

Все функции принимают (session, owner_id) как первые два параметра. Любая
запись/чтение фильтруется по owner_id — пользователь не может прочитать или
изменить данные другого тенанта.

Замена для legacy app/storage.py (SQLite). Старая storage.py остаётся в
репозитории как dead code до удаления отдельной задачей.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AppSetting,
    DefaultTarget,
    OpsMetric,
    OpsProjectTarget,
    OpsTarget,
    PnLTemplateNode,
    ProjectConfig,
    Target,
)


# ---------- Константы (не зависят от owner_id) ----------

OPS_METRICS: list[dict] = [
    {
        "code": "ORD_PER_COURIER_H",
        "label": "Заказов на курьера в час",
        "unit": "зак/ч",
        "field": "orders_per_courier_h",
        "direction": "higher",
        "digits": 2,
    },
    {
        "code": "LATE_CERTS",
        "label": "Сертификаты",
        "unit": "%",
        "field": "late_delivery_certs_pct",
        "count_field": "late_delivery_certs",
        "direction": "lower",
        "digits": 1,
    },
    {
        "code": "PROD_PER_H",
        "label": "Продуктов в час (кухня)",
        "unit": "шт/ч",
        "field": "products_per_h",
        "direction": "higher",
        "digits": 2,
    },
    {
        "code": "REV_PER_PERSON_H",
        "label": "Выручка на человека в час",
        "unit": "₽/ч",
        "field": "revenue_per_person_h",
        "direction": "higher",
        "digits": 0,
    },
]
OPS_METRIC_CODES: list[str] = [m["code"] for m in OPS_METRICS]
OPS_METRIC_FIELD_BY_CODE: dict[str, str] = {m["code"]: m["field"] for m in OPS_METRICS}


# ---------- Targets (per-project) ----------

async def list_targets(
    session: AsyncSession, owner_id: int, project_id: Optional[str] = None
) -> list[dict]:
    stmt = select(Target).where(Target.owner_id == owner_id)
    if project_id:
        stmt = stmt.where(Target.project_id == project_id)
    result = await session.execute(stmt)
    return [
        {"project_id": t.project_id, "metric_code": t.metric_code,
         "target_pct": t.target_pct}
        for t in result.scalars()
    ]


async def upsert_target(
    session: AsyncSession, owner_id: int, project_id: str,
    metric_code: str, target_pct: float,
) -> None:
    stmt = (
        pg_insert(Target)
        .values(
            owner_id=owner_id, project_id=project_id,
            metric_code=metric_code, target_pct=target_pct,
        )
        .on_conflict_do_update(
            constraint="uq_targets_owner_project_metric",
            set_={"target_pct": target_pct, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)


async def delete_target(
    session: AsyncSession, owner_id: int, project_id: str, metric_code: str
) -> None:
    stmt = delete(Target).where(
        Target.owner_id == owner_id,
        Target.project_id == project_id,
        Target.metric_code == metric_code,
    )
    await session.execute(stmt)


# ---------- Default targets ----------

async def list_default_targets(
    session: AsyncSession, owner_id: int
) -> dict[str, float]:
    stmt = select(DefaultTarget).where(DefaultTarget.owner_id == owner_id)
    result = await session.execute(stmt)
    return {dt.metric_code: dt.target_pct for dt in result.scalars()}


async def upsert_default_target(
    session: AsyncSession, owner_id: int, metric_code: str, target_pct: float
) -> None:
    stmt = (
        pg_insert(DefaultTarget)
        .values(owner_id=owner_id, metric_code=metric_code, target_pct=target_pct)
        .on_conflict_do_update(
            index_elements=["owner_id", "metric_code"],
            set_={"target_pct": target_pct, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)


async def delete_default_target(
    session: AsyncSession, owner_id: int, metric_code: str
) -> None:
    stmt = delete(DefaultTarget).where(
        DefaultTarget.owner_id == owner_id,
        DefaultTarget.metric_code == metric_code,
    )
    await session.execute(stmt)


# ---------- App settings (KV) ----------

async def list_settings(session: AsyncSession, owner_id: int) -> dict[str, str]:
    stmt = select(AppSetting).where(AppSetting.owner_id == owner_id)
    result = await session.execute(stmt)
    return {s.key: s.value for s in result.scalars()}


async def get_setting(
    session: AsyncSession, owner_id: int, key: str, default: Optional[str] = None
) -> Optional[str]:
    stmt = select(AppSetting.value).where(
        AppSetting.owner_id == owner_id, AppSetting.key == key
    )
    result = await session.execute(stmt)
    val = result.scalar_one_or_none()
    return val if val is not None else default


async def set_setting(
    session: AsyncSession, owner_id: int, key: str, value: str
) -> None:
    stmt = (
        pg_insert(AppSetting)
        .values(owner_id=owner_id, key=key, value=value)
        .on_conflict_do_update(
            index_elements=["owner_id", "key"],
            set_={"value": value, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)


async def get_bool_setting(
    session: AsyncSession, owner_id: int, key: str, default: bool = False
) -> bool:
    val = await get_setting(session, owner_id, key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on", "y", "t")


# ---------- Projects config ----------

async def list_projects_config(
    session: AsyncSession, owner_id: int
) -> dict[str, dict]:
    stmt = select(ProjectConfig).where(ProjectConfig.owner_id == owner_id)
    result = await session.execute(stmt)
    return {
        p.project_id: {
            "is_active": p.is_active,
            "display_name": p.display_name,
            "sort_order": p.sort_order,
            "dodo_unit_uuid": p.dodo_unit_uuid,
        }
        for p in result.scalars()
    }


_UNSET = object()


async def upsert_project_config(
    session: AsyncSession,
    owner_id: int,
    project_id: str,
    *,
    is_active: Optional[bool] = None,
    display_name: Optional[str] = None,
    sort_order: Optional[int] = None,
    dodo_unit_uuid: Any = _UNSET,
) -> None:
    """None у is_active/sort_order — не менять. Для display_name/dodo_unit_uuid:
    None или '' = очистить, отсутствие = не менять (через _UNSET)."""
    stmt = select(ProjectConfig).where(
        ProjectConfig.owner_id == owner_id,
        ProjectConfig.project_id == project_id,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()

    if existing is None:
        # Create new
        new_active = True if is_active is None else bool(is_active)
        new_name = display_name or None
        new_order = sort_order
        new_uuid = (dodo_unit_uuid or None) if dodo_unit_uuid is not _UNSET else None
        session.add(ProjectConfig(
            owner_id=owner_id, project_id=project_id,
            is_active=new_active, display_name=new_name,
            sort_order=new_order, dodo_unit_uuid=new_uuid,
        ))
    else:
        if is_active is not None:
            existing.is_active = bool(is_active)
        if display_name is not None:
            existing.display_name = display_name or None
        if sort_order is not None:
            existing.sort_order = sort_order
        if dodo_unit_uuid is not _UNSET:
            existing.dodo_unit_uuid = (dodo_unit_uuid or None)
        existing.updated_at = datetime.now(timezone.utc)


async def get_active_project_ids(
    session: AsyncSession, owner_id: int
) -> Optional[set[str]]:
    cfg = await list_projects_config(session, owner_id)
    if not cfg:
        return None
    return {pid for pid, c in cfg.items() if c["is_active"]}


# ---------- Ops metrics ----------

async def list_ops_metrics(
    session: AsyncSession, owner_id: int,
    period_month: Optional[str] = None, project_id: Optional[str] = None,
) -> dict:
    stmt = select(OpsMetric).where(OpsMetric.owner_id == owner_id)
    if period_month:
        stmt = stmt.where(OpsMetric.period_month == period_month)
    if project_id:
        stmt = stmt.where(OpsMetric.project_id == project_id)
    result = await session.execute(stmt)

    out: dict = {}
    for r in result.scalars():
        payload = {
            "orders_per_courier_h": r.orders_per_courier_h,
            "products_per_h": r.products_per_h,
            "revenue_per_person_h": r.revenue_per_person_h,
            "late_delivery_certs": r.late_delivery_certs,
            "delivery_orders_count": r.delivery_orders_count,
            "late_delivery_certs_pct": r.late_delivery_certs_pct,
        }
        if period_month is None:
            out.setdefault(r.project_id, {})[r.period_month] = payload
        else:
            out[r.project_id] = payload
    return out


async def upsert_ops_metric(
    session: AsyncSession, owner_id: int, project_id: str, period_month: str,
    *,
    orders_per_courier_h: Optional[float] = None,
    products_per_h: Optional[float] = None,
    revenue_per_person_h: Optional[float] = None,
    late_delivery_certs: Optional[int] = None,
    delivery_orders_count: Optional[int] = None,
    late_delivery_certs_pct: Optional[float] = None,
) -> None:
    if (
        late_delivery_certs_pct is None
        and late_delivery_certs is not None
        and delivery_orders_count is not None
        and delivery_orders_count > 0
    ):
        late_delivery_certs_pct = (
            float(late_delivery_certs) / float(delivery_orders_count) * 100.0
        )

    stmt = select(OpsMetric).where(
        OpsMetric.owner_id == owner_id,
        OpsMetric.project_id == project_id,
        OpsMetric.period_month == period_month,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is None:
        session.add(OpsMetric(
            owner_id=owner_id, project_id=project_id, period_month=period_month,
            orders_per_courier_h=orders_per_courier_h,
            products_per_h=products_per_h,
            revenue_per_person_h=revenue_per_person_h,
            late_delivery_certs=int(late_delivery_certs) if late_delivery_certs is not None else None,
            delivery_orders_count=int(delivery_orders_count) if delivery_orders_count is not None else None,
            late_delivery_certs_pct=float(late_delivery_certs_pct) if late_delivery_certs_pct is not None else None,
        ))
    else:
        if orders_per_courier_h is not None:
            existing.orders_per_courier_h = orders_per_courier_h
        if products_per_h is not None:
            existing.products_per_h = products_per_h
        if revenue_per_person_h is not None:
            existing.revenue_per_person_h = revenue_per_person_h
        if late_delivery_certs is not None:
            existing.late_delivery_certs = int(late_delivery_certs)
        if delivery_orders_count is not None:
            existing.delivery_orders_count = int(delivery_orders_count)
        if late_delivery_certs_pct is not None:
            existing.late_delivery_certs_pct = float(late_delivery_certs_pct)
        existing.updated_at = datetime.now(timezone.utc)


async def delete_ops_metric(
    session: AsyncSession, owner_id: int, project_id: str, period_month: str
) -> None:
    stmt = delete(OpsMetric).where(
        OpsMetric.owner_id == owner_id,
        OpsMetric.project_id == project_id,
        OpsMetric.period_month == period_month,
    )
    await session.execute(stmt)


async def list_ops_metrics_months(
    session: AsyncSession, owner_id: int, project_id: Optional[str] = None
) -> list[str]:
    stmt = (
        select(OpsMetric.period_month)
        .where(OpsMetric.owner_id == owner_id)
        .distinct()
        .order_by(OpsMetric.period_month.desc())
    )
    if project_id:
        stmt = stmt.where(OpsMetric.project_id == project_id)
    result = await session.execute(stmt)
    return [m for (m,) in result.all()]


# ---------- Ops targets ----------

async def list_ops_targets(
    session: AsyncSession, owner_id: int
) -> dict[str, float]:
    stmt = select(OpsTarget).where(OpsTarget.owner_id == owner_id)
    result = await session.execute(stmt)
    return {t.metric_code: t.target_value for t in result.scalars()}


async def upsert_ops_target(
    session: AsyncSession, owner_id: int, metric_code: str, target_value: float
) -> None:
    stmt = (
        pg_insert(OpsTarget)
        .values(owner_id=owner_id, metric_code=metric_code, target_value=target_value)
        .on_conflict_do_update(
            index_elements=["owner_id", "metric_code"],
            set_={"target_value": target_value, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)


async def delete_ops_target(
    session: AsyncSession, owner_id: int, metric_code: str
) -> None:
    stmt = delete(OpsTarget).where(
        OpsTarget.owner_id == owner_id, OpsTarget.metric_code == metric_code
    )
    await session.execute(stmt)


# ---------- Ops project targets ----------

async def list_ops_project_targets(
    session: AsyncSession, owner_id: int, project_id: Optional[str] = None
) -> list[dict]:
    stmt = select(OpsProjectTarget).where(OpsProjectTarget.owner_id == owner_id)
    if project_id:
        stmt = stmt.where(OpsProjectTarget.project_id == project_id)
    result = await session.execute(stmt)
    return [
        {"project_id": t.project_id, "metric_code": t.metric_code,
         "target_value": t.target_value}
        for t in result.scalars()
    ]


async def ops_project_targets_map(
    session: AsyncSession, owner_id: int
) -> dict[str, dict[str, float]]:
    rows = await list_ops_project_targets(session, owner_id)
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["project_id"], {})[r["metric_code"]] = r["target_value"]
    return out


async def upsert_ops_project_target(
    session: AsyncSession, owner_id: int, project_id: str,
    metric_code: str, target_value: float,
) -> None:
    stmt = (
        pg_insert(OpsProjectTarget)
        .values(
            owner_id=owner_id, project_id=project_id,
            metric_code=metric_code, target_value=target_value,
        )
        .on_conflict_do_update(
            index_elements=["owner_id", "project_id", "metric_code"],
            set_={"target_value": target_value, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)


async def delete_ops_project_target(
    session: AsyncSession, owner_id: int, project_id: str, metric_code: str
) -> None:
    stmt = delete(OpsProjectTarget).where(
        OpsProjectTarget.owner_id == owner_id,
        OpsProjectTarget.project_id == project_id,
        OpsProjectTarget.metric_code == metric_code,
    )
    await session.execute(stmt)


def effective_ops_target(
    project_id: str, metric_code: str,
    overrides: dict[str, dict[str, float]],
    defaults: dict[str, float],
) -> Optional[float]:
    """Per-project override > default > None. Чистая функция, без БД."""
    per = overrides.get(project_id, {})
    if metric_code in per:
        return per[metric_code]
    return defaults.get(metric_code)


# ---------- PnL template ----------
# Привязан к planfact_key_id. Юзеры с одним PF-ключом видят один шаблон.

async def list_template_nodes(
    session: AsyncSession, planfact_key_id: int
) -> list[dict]:
    stmt = (
        select(PnLTemplateNode)
        .where(PnLTemplateNode.planfact_key_id == planfact_key_id)
        .order_by(PnLTemplateNode.sort_order)
    )
    result = await session.execute(stmt)
    return [
        {
            "id": n.id,
            "parent_id": n.parent_id,
            "depth": n.depth,
            "title": n.title,
            "path": [s for s in (n.path or "").split(" / ") if s],
            "path_lc": n.path_lc,
            "is_calc": bool(n.is_calc),
            "is_leaf": bool(n.is_leaf),
            "pnl_code": n.pnl_code,
            "sort_order": n.sort_order,
        }
        for n in result.scalars()
    ]


async def template_is_empty(session: AsyncSession, planfact_key_id: int) -> bool:
    stmt = select(PnLTemplateNode.id).where(
        PnLTemplateNode.planfact_key_id == planfact_key_id
    ).limit(1)
    result = await session.execute(stmt)
    return result.first() is None


async def template_path_to_code(
    session: AsyncSession, planfact_key_id: int
) -> dict[str, str]:
    stmt = select(PnLTemplateNode.path_lc, PnLTemplateNode.pnl_code).where(
        PnLTemplateNode.planfact_key_id == planfact_key_id,
        PnLTemplateNode.is_calc == False,  # noqa: E712
        PnLTemplateNode.pnl_code.isnot(None),
        PnLTemplateNode.pnl_code != "",
    )
    result = await session.execute(stmt)
    return {p: c for (p, c) in result.all()}


async def template_leaf_title_to_code(
    session: AsyncSession, planfact_key_id: int
) -> dict[str, str]:
    nodes = await list_template_nodes(session, planfact_key_id)
    out: dict[str, str] = {}
    for n in nodes:
        if n["is_calc"] or not n["pnl_code"]:
            continue
        leaf = (n["path_lc"].split(" / ") or [""])[-1].strip()
        if leaf:
            out[leaf] = n["pnl_code"]
    return out


async def replace_template_tree(
    session: AsyncSession, planfact_key_id: int, nodes: list[dict]
) -> int:
    """Полная замена шаблона для ключа PlanFact. Не трогает чужих ключей."""
    # Удаляем всё текущее
    await session.execute(
        delete(PnLTemplateNode).where(
            PnLTemplateNode.planfact_key_id == planfact_key_id
        )
    )
    await session.flush()  # чтобы DELETE применился перед INSERT

    idx_to_id: dict[int, int] = {}
    for i, n in enumerate(nodes):
        parent_id = (
            idx_to_id.get(n["parent_idx"])
            if n.get("parent_idx") is not None
            else None
        )
        path_str = " / ".join(n["path"])
        node = PnLTemplateNode(
            planfact_key_id=planfact_key_id,
            parent_id=parent_id,
            depth=int(n["depth"]),
            title=n["title"],
            path=path_str,
            path_lc=path_str.lower(),
            is_calc=bool(n.get("is_calc")),
            is_leaf=bool(n.get("is_leaf")),
            pnl_code=(n.get("pnl_code") or None),
            sort_order=int(n.get("sort_order") or (i + 1)),
        )
        session.add(node)
        await session.flush()  # получаем node.id для следующих parent_id
        idx_to_id[i] = node.id
    return len(nodes)


async def update_template_node_code(
    session: AsyncSession, planfact_key_id: int,
    node_id: int, pnl_code: Optional[str],
) -> bool:
    stmt = (
        update(PnLTemplateNode)
        .where(
            PnLTemplateNode.id == node_id,
            PnLTemplateNode.planfact_key_id == planfact_key_id,
        )
        .values(pnl_code=(pnl_code or None), updated_at=datetime.now(timezone.utc))
    )
    result = await session.execute(stmt)
    return result.rowcount > 0


async def clear_template(session: AsyncSession, planfact_key_id: int) -> None:
    await session.execute(
        delete(PnLTemplateNode).where(
            PnLTemplateNode.planfact_key_id == planfact_key_id
        )
    )
