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
    CacheHistory,
    DefaultTarget,
    OpsMetric,
    OpsProjectTarget,
    OpsTarget,
    PnLMetric,
    PnLTemplateNode,
    ProjectConfig,
    Target,
    UserProjectVisibility,
)


# ---------- Константы (не зависят от owner_id) ----------

OPS_METRICS: list[dict] = [
    {
        "code": "ORD_PER_COURIER_H",
        # NBSP между «на» и «курьера» — чтобы 2-строчный перенос делал
        # «Заказов» / «на курьера» вместо 3 строк.
        "label": "Заказов на курьера",
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
        "label": "Продуктов в час",
        "unit": "шт/ч",
        "field": "products_per_h",
        "direction": "higher",
        "digits": 2,
    },
    {
        "code": "REV_PER_PERSON_H",
        "label": "Выручка на человека",
        "unit": "₽/ч",
        "field": "revenue_per_person_h",
        "direction": "higher",
        "digits": 0,
    },
    # S16: метрики из /delivery/statistics. Все 4 поля приходят за один
    # запрос — добавление дешёвое.
    {
        "code": "ORD_PER_TRIP",
        "label": "Заказов на поездку",
        "unit": "шт",
        "field": "orders_per_trip",
        "direction": "higher",
        "digits": 2,
    },
    {
        "code": "AOT",
        # Average Order Trip — стандартный Dodo-термин для времени курьера
        # с заказом в пути. Значение в секундах, формат на UI — mm:ss.
        "label": "Время в пути (AOT)",
        "unit": "",
        "field": "avg_order_trip_time_sec",
        "format": "mm_ss",
        "direction": "lower",
        "digits": 0,
    },
    {
        "code": "COURIER_UTIL",
        # tripsDuration / couriersShiftsDuration — доля времени курьеров
        # «в поездке» от их смены. Higher лучше: меньше простоя.
        "label": "Загрузка курьеров",
        "unit": "%",
        "field": "courier_utilization_pct",
        "direction": "higher",
        "digits": 1,
    },
    # S16.1/S16.2: время готовки расщеплено по каналам, в секундах.
    # Delivery → /delivery/statistics.avgCookingTime
    # Restaurant → /production/orders-handover-statistics?salesChannels=DineIn
    {
        "code": "COOK_TIME_DELIVERY",
        "label": "Готовка · доставка",
        "unit": "",
        "field": "avg_cooking_time_delivery_sec",
        "format": "mm_ss",
        "direction": "lower",
        "digits": 0,
    },
    {
        "code": "COOK_TIME_RESTAURANT",
        "label": "Готовка · ресторан",
        "unit": "",
        "field": "avg_cooking_time_restaurant_sec",
        "format": "mm_ss",
        "direction": "lower",
        "digits": 0,
    },
]
OPS_METRIC_CODES: list[str] = [m["code"] for m in OPS_METRICS]
OPS_METRIC_FIELD_BY_CODE: dict[str, str] = {m["code"]: m["field"] for m in OPS_METRICS}


# ---------- Targets (per-project, per planfact_key) ----------
# Метрики (UC/LC/DC/...) теперь общие на ключ — таргеты тоже.

# S14.1+: period_month='__default__' = «применяется ко всем месяцам».
# Конкретный 'YYYY-MM' = override для этого месяца.
DEFAULT_PERIOD = "__default__"


async def list_targets(
    session: AsyncSession, planfact_key_id: int,
    project_id: Optional[str] = None,
    period_month: str = DEFAULT_PERIOD,
) -> list[dict]:
    """Список per-project таргетов для одного period_month.

    Default-poведение (period_month='__default__'): возвращает «общие»
    таргеты применимые ко всем месяцам. UI на /settings → Цели в режиме
    «Все месяцы» использует этот вариант.
    """
    stmt = select(Target).where(
        Target.planfact_key_id == planfact_key_id,
        Target.period_month == period_month,
    )
    if project_id:
        stmt = stmt.where(Target.project_id == project_id)
    result = await session.execute(stmt)
    return [
        {"project_id": t.project_id, "metric_code": t.metric_code,
         "target_pct": t.target_pct, "period_month": t.period_month}
        for t in result.scalars()
    ]


async def upsert_target(
    session: AsyncSession, planfact_key_id: int, project_id: str,
    metric_code: str, target_pct: float,
    period_month: str = DEFAULT_PERIOD,
) -> None:
    stmt = (
        pg_insert(Target)
        .values(
            planfact_key_id=planfact_key_id, project_id=project_id,
            metric_code=metric_code, target_pct=target_pct,
            period_month=period_month,
        )
        .on_conflict_do_update(
            index_elements=["planfact_key_id", "project_id", "metric_code", "period_month"],
            set_={"target_pct": target_pct, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)


async def delete_target(
    session: AsyncSession, planfact_key_id: int,
    project_id: str, metric_code: str,
    period_month: str = DEFAULT_PERIOD,
) -> None:
    stmt = delete(Target).where(
        Target.planfact_key_id == planfact_key_id,
        Target.project_id == project_id,
        Target.metric_code == metric_code,
        Target.period_month == period_month,
    )
    await session.execute(stmt)


# ---------- Default targets (per planfact_key) ----------

async def list_default_targets(
    session: AsyncSession, planfact_key_id: int,
    period_month: str = DEFAULT_PERIOD,
) -> dict[str, float]:
    stmt = select(DefaultTarget).where(
        DefaultTarget.planfact_key_id == planfact_key_id,
        DefaultTarget.period_month == period_month,
    )
    result = await session.execute(stmt)
    return {dt.metric_code: dt.target_pct for dt in result.scalars()}


async def upsert_default_target(
    session: AsyncSession, planfact_key_id: int,
    metric_code: str, target_pct: float,
    period_month: str = DEFAULT_PERIOD,
) -> None:
    stmt = (
        pg_insert(DefaultTarget)
        .values(
            planfact_key_id=planfact_key_id,
            metric_code=metric_code, target_pct=target_pct,
            period_month=period_month,
        )
        .on_conflict_do_update(
            index_elements=["planfact_key_id", "metric_code", "period_month"],
            set_={"target_pct": target_pct, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)


async def delete_default_target(
    session: AsyncSession, planfact_key_id: int, metric_code: str,
    period_month: str = DEFAULT_PERIOD,
) -> None:
    stmt = delete(DefaultTarget).where(
        DefaultTarget.planfact_key_id == planfact_key_id,
        DefaultTarget.metric_code == metric_code,
        DefaultTarget.period_month == period_month,
    )
    await session.execute(stmt)


# ---------- Effective targets с fallback (S14.3) ----------

async def effective_targets_for_period(
    session: AsyncSession, planfact_key_id: int, period_month: Optional[str],
) -> tuple[list[dict], dict[str, float]]:
    """Эффективные таргеты для конкретного месяца с fallback к __default__.

    period_month=None или '__default__' → возвращаем только дефолтные таргеты.
    period_month='YYYY-MM' → берём month-specific, для отсутствующих fallback
    к default. Этот двухслойный merge нужен в /api/pnl, где юзер видит
    «цель 32%» по UC: если на месяц задана своя — её показываем, иначе
    дефолт ключа.

    Возвращает кортеж:
      (per_project_targets: list[{project_id, metric_code, target_pct}],
       default_targets:     dict{metric_code: target_pct})
    """
    pm = period_month or DEFAULT_PERIOD

    # 1) Defaults: month-specific перебивают __default__
    defaults_default = await list_default_targets(
        session, planfact_key_id, period_month=DEFAULT_PERIOD,
    )
    defaults: dict[str, float] = dict(defaults_default)
    if pm != DEFAULT_PERIOD:
        defaults_month = await list_default_targets(
            session, planfact_key_id, period_month=pm,
        )
        defaults.update(defaults_month)

    # 2) Per-project: month-specific перебивают __default__-rows
    per_proj_default = await list_targets(
        session, planfact_key_id, period_month=DEFAULT_PERIOD,
    )
    by_key: dict[tuple[str, str], dict] = {
        (t["project_id"], t["metric_code"]): t for t in per_proj_default
    }
    if pm != DEFAULT_PERIOD:
        per_proj_month = await list_targets(
            session, planfact_key_id, period_month=pm,
        )
        for t in per_proj_month:
            by_key[(t["project_id"], t["metric_code"])] = t

    return list(by_key.values()), defaults


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


# ---------- Projects config (per planfact_key) ----------
# Конфигурация проектов общая для всех пользователей с одним PF-ключом.

async def list_projects_config(
    session: AsyncSession, planfact_key_id: int
) -> dict[str, dict]:
    stmt = select(ProjectConfig).where(
        ProjectConfig.planfact_key_id == planfact_key_id
    )
    result = await session.execute(stmt)
    return {
        p.project_id: {
            "is_active": p.is_active,
            "is_admin_managed": p.is_admin_managed,
            "display_name": p.display_name,
            "sort_order": p.sort_order,
            "dodo_unit_uuid": p.dodo_unit_uuid,
        }
        for p in result.scalars()
    }


_UNSET = object()


async def upsert_project_config(
    session: AsyncSession,
    planfact_key_id: int,
    project_id: str,
    *,
    is_active: Optional[bool] = None,
    is_admin_managed: Optional[bool] = None,
    display_name: Optional[str] = None,
    sort_order: Optional[int] = None,
    dodo_unit_uuid: Any = _UNSET,
) -> None:
    """None у bool/sort_order — не менять. Для display_name/dodo_unit_uuid:
    None или '' = очистить, отсутствие = не менять (через _UNSET)."""
    stmt = select(ProjectConfig).where(
        ProjectConfig.planfact_key_id == planfact_key_id,
        ProjectConfig.project_id == project_id,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()

    if existing is None:
        # Create new
        new_active = True if is_active is None else bool(is_active)
        new_admin_managed = True if is_admin_managed is None else bool(is_admin_managed)
        new_name = display_name or None
        new_order = sort_order
        new_uuid = (dodo_unit_uuid or None) if dodo_unit_uuid is not _UNSET else None
        session.add(ProjectConfig(
            planfact_key_id=planfact_key_id, project_id=project_id,
            is_active=new_active, is_admin_managed=new_admin_managed,
            display_name=new_name,
            sort_order=new_order, dodo_unit_uuid=new_uuid,
        ))
    else:
        if is_active is not None:
            existing.is_active = bool(is_active)
        if is_admin_managed is not None:
            existing.is_admin_managed = bool(is_admin_managed)
        if display_name is not None:
            existing.display_name = display_name or None
        if sort_order is not None:
            existing.sort_order = sort_order
        if dodo_unit_uuid is not _UNSET:
            existing.dodo_unit_uuid = (dodo_unit_uuid or None)
        existing.updated_at = datetime.now(timezone.utc)


async def get_active_project_ids(
    session: AsyncSession, planfact_key_id: int
) -> Optional[set[str]]:
    cfg = await list_projects_config(session, planfact_key_id)
    if not cfg:
        return None
    return {pid for pid, c in cfg.items() if c["is_active"]}


# ---------- User project visibility (per-user override) ----------

async def get_user_hidden_projects(
    session: AsyncSession, owner_id: int
) -> set[str]:
    """Список project_id, скрытых лично для этого юзера. По умолчанию все
    проекты видимы — пуст set, если для юзера нет ни одной записи с
    is_visible=False."""
    stmt = select(UserProjectVisibility.project_id).where(
        UserProjectVisibility.owner_id == owner_id,
        UserProjectVisibility.is_visible == False,  # noqa: E712
    )
    result = await session.execute(stmt)
    return {pid for (pid,) in result.all()}


async def list_user_visibility(
    session: AsyncSession, owner_id: int
) -> dict[str, bool]:
    """Все записи visibility юзера. {project_id → is_visible}.
    Отсутствующие в этом dict проекты считаются видимыми (default True)."""
    stmt = select(UserProjectVisibility).where(
        UserProjectVisibility.owner_id == owner_id
    )
    result = await session.execute(stmt)
    return {v.project_id: v.is_visible for v in result.scalars()}


async def set_user_visibility(
    session: AsyncSession, owner_id: int, project_id: str, is_visible: bool,
) -> None:
    """Установить флаг видимости. По умолчанию проект видим, поэтому если
    is_visible=True и записи нет — можно её даже не создавать. Но для
    единообразия всё равно UPSERT'им — это даёт явный аудит-трейл."""
    stmt = (
        pg_insert(UserProjectVisibility)
        .values(
            owner_id=owner_id, project_id=project_id, is_visible=is_visible,
        )
        .on_conflict_do_update(
            index_elements=["owner_id", "project_id"],
            set_={
                "is_visible": is_visible,
                "updated_at": datetime.now(timezone.utc),
            },
        )
    )
    await session.execute(stmt)


# ---------- Ops metrics ----------

async def list_ops_metrics(
    session: AsyncSession, planfact_key_id: int,
    period_month: Optional[str] = None, project_id: Optional[str] = None,
) -> dict:
    """Ops-метрики ключа (S11.6: per planfact_key_id)."""
    stmt = select(OpsMetric).where(OpsMetric.planfact_key_id == planfact_key_id)
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
            # S16 / S16.2
            "orders_per_trip": r.orders_per_trip,
            "courier_utilization_pct": r.courier_utilization_pct,
            "avg_order_trip_time_sec": r.avg_order_trip_time_sec,
            "avg_cooking_time_delivery_sec": r.avg_cooking_time_delivery_sec,
            "avg_cooking_time_restaurant_sec": r.avg_cooking_time_restaurant_sec,
        }
        if period_month is None:
            out.setdefault(r.project_id, {})[r.period_month] = payload
        else:
            out[r.project_id] = payload
    return out


async def upsert_ops_metric(
    session: AsyncSession, planfact_key_id: int, project_id: str, period_month: str,
    *,
    orders_per_courier_h: Optional[float] = None,
    products_per_h: Optional[float] = None,
    revenue_per_person_h: Optional[float] = None,
    late_delivery_certs: Optional[int] = None,
    delivery_orders_count: Optional[int] = None,
    late_delivery_certs_pct: Optional[float] = None,
    # S16 / S16.2
    orders_per_trip: Optional[float] = None,
    courier_utilization_pct: Optional[float] = None,
    avg_order_trip_time_sec: Optional[int] = None,
    avg_cooking_time_delivery_sec: Optional[int] = None,
    avg_cooking_time_restaurant_sec: Optional[int] = None,
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
        OpsMetric.planfact_key_id == planfact_key_id,
        OpsMetric.project_id == project_id,
        OpsMetric.period_month == period_month,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is None:
        session.add(OpsMetric(
            planfact_key_id=planfact_key_id,
            project_id=project_id, period_month=period_month,
            orders_per_courier_h=orders_per_courier_h,
            products_per_h=products_per_h,
            revenue_per_person_h=revenue_per_person_h,
            late_delivery_certs=int(late_delivery_certs) if late_delivery_certs is not None else None,
            delivery_orders_count=int(delivery_orders_count) if delivery_orders_count is not None else None,
            late_delivery_certs_pct=float(late_delivery_certs_pct) if late_delivery_certs_pct is not None else None,
            orders_per_trip=orders_per_trip,
            courier_utilization_pct=courier_utilization_pct,
            avg_order_trip_time_sec=int(avg_order_trip_time_sec) if avg_order_trip_time_sec is not None else None,
            avg_cooking_time_delivery_sec=int(avg_cooking_time_delivery_sec) if avg_cooking_time_delivery_sec is not None else None,
            avg_cooking_time_restaurant_sec=int(avg_cooking_time_restaurant_sec) if avg_cooking_time_restaurant_sec is not None else None,
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
        if orders_per_trip is not None:
            existing.orders_per_trip = orders_per_trip
        if courier_utilization_pct is not None:
            existing.courier_utilization_pct = courier_utilization_pct
        if avg_order_trip_time_sec is not None:
            existing.avg_order_trip_time_sec = int(avg_order_trip_time_sec)
        if avg_cooking_time_delivery_sec is not None:
            existing.avg_cooking_time_delivery_sec = int(avg_cooking_time_delivery_sec)
        if avg_cooking_time_restaurant_sec is not None:
            existing.avg_cooking_time_restaurant_sec = int(avg_cooking_time_restaurant_sec)
        existing.updated_at = datetime.now(timezone.utc)


async def delete_ops_metric(
    session: AsyncSession, planfact_key_id: int, project_id: str, period_month: str
) -> None:
    stmt = delete(OpsMetric).where(
        OpsMetric.planfact_key_id == planfact_key_id,
        OpsMetric.project_id == project_id,
        OpsMetric.period_month == period_month,
    )
    await session.execute(stmt)


async def ops_last_synced_at(
    session: AsyncSession, planfact_key_id: int, period_month: str,
) -> Optional[datetime]:
    """max(updated_at) по ops_metrics ключа за период. None если синков
    не было. После S11.6 общий для всех юзеров одного PF-ключа: один
    юзер запустил синк — все остальные видят свежий бейдж."""
    from sqlalchemy import func
    stmt = (
        select(func.max(OpsMetric.updated_at))
        .where(
            OpsMetric.planfact_key_id == planfact_key_id,
            OpsMetric.period_month == period_month,
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_ops_metrics_months(
    session: AsyncSession, planfact_key_id: int, project_id: Optional[str] = None
) -> list[str]:
    stmt = (
        select(OpsMetric.period_month)
        .where(OpsMetric.planfact_key_id == planfact_key_id)
        .distinct()
        .order_by(OpsMetric.period_month.desc())
    )
    if project_id:
        stmt = stmt.where(OpsMetric.project_id == project_id)
    result = await session.execute(stmt)
    return [m for (m,) in result.all()]


# ---------- Ops targets (per PF-key, S11.6) ----------

async def list_ops_targets(
    session: AsyncSession, planfact_key_id: int,
    period_month: str = DEFAULT_PERIOD,
) -> dict[str, float]:
    stmt = select(OpsTarget).where(
        OpsTarget.planfact_key_id == planfact_key_id,
        OpsTarget.period_month == period_month,
    )
    result = await session.execute(stmt)
    return {t.metric_code: t.target_value for t in result.scalars()}


async def upsert_ops_target(
    session: AsyncSession, planfact_key_id: int,
    metric_code: str, target_value: float,
    period_month: str = DEFAULT_PERIOD,
) -> None:
    stmt = (
        pg_insert(OpsTarget)
        .values(
            planfact_key_id=planfact_key_id,
            metric_code=metric_code, target_value=target_value,
            period_month=period_month,
        )
        .on_conflict_do_update(
            index_elements=["planfact_key_id", "metric_code", "period_month"],
            set_={"target_value": target_value, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)


async def delete_ops_target(
    session: AsyncSession, planfact_key_id: int, metric_code: str,
    period_month: str = DEFAULT_PERIOD,
) -> None:
    stmt = delete(OpsTarget).where(
        OpsTarget.planfact_key_id == planfact_key_id,
        OpsTarget.metric_code == metric_code,
        OpsTarget.period_month == period_month,
    )
    await session.execute(stmt)


# ---------- Ops project targets (per PF-key, S11.6) ----------

async def list_ops_project_targets(
    session: AsyncSession, planfact_key_id: int,
    project_id: Optional[str] = None,
    period_month: str = DEFAULT_PERIOD,
) -> list[dict]:
    stmt = select(OpsProjectTarget).where(
        OpsProjectTarget.planfact_key_id == planfact_key_id,
        OpsProjectTarget.period_month == period_month,
    )
    if project_id:
        stmt = stmt.where(OpsProjectTarget.project_id == project_id)
    result = await session.execute(stmt)
    return [
        {"project_id": t.project_id, "metric_code": t.metric_code,
         "target_value": t.target_value, "period_month": t.period_month}
        for t in result.scalars()
    ]


async def ops_project_targets_map(
    session: AsyncSession, planfact_key_id: int,
    period_month: str = DEFAULT_PERIOD,
) -> dict[str, dict[str, float]]:
    """Helper для build_pnl: project_id → {metric_code: target_value}.

    period_month: если 'YYYY-MM', результат строится с fallback (month → default).
    """
    out: dict[str, dict[str, float]] = {}
    # Сначала default-уровень, потом перетираем month-specific.
    default_rows = await list_ops_project_targets(
        session, planfact_key_id, period_month=DEFAULT_PERIOD,
    )
    for r in default_rows:
        out.setdefault(r["project_id"], {})[r["metric_code"]] = r["target_value"]
    if period_month and period_month != DEFAULT_PERIOD:
        month_rows = await list_ops_project_targets(
            session, planfact_key_id, period_month=period_month,
        )
        for r in month_rows:
            out.setdefault(r["project_id"], {})[r["metric_code"]] = r["target_value"]
    return out


async def upsert_ops_project_target(
    session: AsyncSession, planfact_key_id: int, project_id: str,
    metric_code: str, target_value: float,
    period_month: str = DEFAULT_PERIOD,
) -> None:
    stmt = (
        pg_insert(OpsProjectTarget)
        .values(
            planfact_key_id=planfact_key_id, project_id=project_id,
            metric_code=metric_code, target_value=target_value,
            period_month=period_month,
        )
        .on_conflict_do_update(
            index_elements=["planfact_key_id", "project_id", "metric_code", "period_month"],
            set_={"target_value": target_value, "updated_at": datetime.now(timezone.utc)},
        )
    )
    await session.execute(stmt)


async def delete_ops_project_target(
    session: AsyncSession, planfact_key_id: int,
    project_id: str, metric_code: str,
    period_month: str = DEFAULT_PERIOD,
) -> None:
    stmt = delete(OpsProjectTarget).where(
        OpsProjectTarget.planfact_key_id == planfact_key_id,
        OpsProjectTarget.project_id == project_id,
        OpsProjectTarget.metric_code == metric_code,
        OpsProjectTarget.period_month == period_month,
    )
    await session.execute(stmt)


async def effective_ops_targets_for_period(
    session: AsyncSession, planfact_key_id: int,
    period_month: Optional[str],
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Эффективные ops-таргеты для конкретного месяца.

    Возвращает (defaults_by_metric, by_project_by_metric) с fallback
    month → default. Симметрично effective_targets_for_period для P&L.
    """
    pm = period_month or DEFAULT_PERIOD
    defaults_def = await list_ops_targets(
        session, planfact_key_id, period_month=DEFAULT_PERIOD,
    )
    defaults: dict[str, float] = dict(defaults_def)
    if pm != DEFAULT_PERIOD:
        defaults.update(
            await list_ops_targets(session, planfact_key_id, period_month=pm)
        )
    proj_map = await ops_project_targets_map(
        session, planfact_key_id, period_month=pm,
    )
    return defaults, proj_map


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
            "line_no": n.line_no,
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
    """Полная замена шаблона для ключа PlanFact. Не трогает чужих ключей.

    line_no стабилен между импортами: для строк с совпавшим path сохраняем
    номер из старого шаблона. Новым строкам — следующий свободный.
    Это важно, чтобы формулы в pnl_metrics не «съехали» при reimport'е.
    """
    # 1. Запоминаем старые line_no по path
    old_rows = (
        await session.execute(
            select(PnLTemplateNode.path_lc, PnLTemplateNode.line_no).where(
                PnLTemplateNode.planfact_key_id == planfact_key_id
            )
        )
    ).all()
    old_line_no_by_path: dict[str, int] = {p: ln for p, ln in old_rows}
    next_line_no = max(old_line_no_by_path.values(), default=0) + 1

    # 2. Удаляем всё текущее
    await session.execute(
        delete(PnLTemplateNode).where(
            PnLTemplateNode.planfact_key_id == planfact_key_id
        )
    )
    await session.flush()  # чтобы DELETE применился перед INSERT

    # 3. Вставляем заново, проставляя line_no с сохранением для совпавших path
    idx_to_id: dict[int, int] = {}
    used_line_nos: set[int] = set()
    for i, n in enumerate(nodes):
        parent_id = (
            idx_to_id.get(n["parent_idx"])
            if n.get("parent_idx") is not None
            else None
        )
        path_str = " / ".join(n["path"])
        path_lc = path_str.lower()
        # line_no: старый по path, иначе новый из счётчика. Конфликт
        # (в старом шаблоне был дубль path с одним номером) — даём новый.
        line_no = old_line_no_by_path.get(path_lc)
        if line_no is None or line_no in used_line_nos:
            line_no = next_line_no
            next_line_no += 1
        used_line_nos.add(line_no)
        node = PnLTemplateNode(
            planfact_key_id=planfact_key_id,
            parent_id=parent_id,
            depth=int(n["depth"]),
            title=n["title"],
            path=path_str,
            path_lc=path_lc,
            is_calc=bool(n.get("is_calc")),
            is_leaf=bool(n.get("is_leaf")),
            pnl_code=(n.get("pnl_code") or None),
            sort_order=int(n.get("sort_order") or (i + 1)),
            line_no=line_no,
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


# ---------- P&L metrics (формулы) ----------
# Per planfact_key. Read для всех с ключом, write — admin (контроль в роутере).

async def list_metrics(
    session: AsyncSession, planfact_key_id: int
) -> list[dict]:
    stmt = (
        select(PnLMetric)
        .where(PnLMetric.planfact_key_id == planfact_key_id)
        .order_by(PnLMetric.sort_order, PnLMetric.code)
    )
    result = await session.execute(stmt)
    return [
        {
            "code": m.code,
            "label": m.label,
            "formula": m.formula,
            "is_target": bool(m.is_target),
            "format": m.format,
            "sort_order": m.sort_order,
            "min_visibility_level": m.min_visibility_level,
            "is_visible": bool(m.is_visible),
        }
        for m in result.scalars()
    ]


async def upsert_metric(
    session: AsyncSession, planfact_key_id: int, *,
    code: str, label: str, formula: str,
    is_target: bool, format: str, sort_order: int,
    min_visibility_level: int = 0,
    is_visible: bool = True,
) -> None:
    stmt = (
        pg_insert(PnLMetric)
        .values(
            planfact_key_id=planfact_key_id,
            code=code, label=label, formula=formula,
            is_target=is_target, format=format, sort_order=sort_order,
            min_visibility_level=min_visibility_level,
            is_visible=is_visible,
        )
        .on_conflict_do_update(
            index_elements=["planfact_key_id", "code"],
            set_={
                "label": label,
                "formula": formula,
                "is_target": is_target,
                "format": format,
                "sort_order": sort_order,
                "min_visibility_level": min_visibility_level,
                "is_visible": is_visible,
                "updated_at": datetime.now(timezone.utc),
            },
        )
    )
    await session.execute(stmt)


async def delete_metric(
    session: AsyncSession, planfact_key_id: int, code: str
) -> None:
    await session.execute(
        delete(PnLMetric).where(
            PnLMetric.planfact_key_id == planfact_key_id,
            PnLMetric.code == code,
        )
    )


async def template_line_nos(
    session: AsyncSession, planfact_key_id: int
) -> set[int]:
    """Множество line_no'ов в шаблоне — для валидации формул при сохранении."""
    stmt = select(PnLTemplateNode.line_no).where(
        PnLTemplateNode.planfact_key_id == planfact_key_id
    )
    result = await session.execute(stmt)
    return {ln for (ln,) in result.all()}


# ---------- Cache history (immutable снэпшоты закрытых месяцев) ----------

CACHE_KIND_PLANFACT_PNL = "planfact_pnl"


def is_period_in_live_window(
    period_month: str, current_month: str, live_months_window: int,
) -> bool:
    """Возвращает True если period попадает в live-окно (текущий + N-1
    предыдущих). period_month/current_month в формате 'YYYY-MM'.
    Будущие периоды (period > current) тоже считаются live (никогда не
    кэшируем то, чего ещё не было)."""
    try:
        py, pm = map(int, period_month.split("-"))
        cy, cm = map(int, current_month.split("-"))
    except (ValueError, AttributeError):
        return True  # на случай странных форматов — fallback на live
    diff = (cy - py) * 12 + (cm - pm)
    return diff < max(1, live_months_window)


async def get_cache_entry(
    session: AsyncSession,
    planfact_key_id: int,
    period_month: str,
    kind: str = CACHE_KIND_PLANFACT_PNL,
) -> Optional[dict]:
    """Прочитать payload снэпшота. None если записи нет."""
    stmt = select(CacheHistory).where(
        CacheHistory.planfact_key_id == planfact_key_id,
        CacheHistory.kind == kind,
        CacheHistory.period_month == period_month,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row.payload if row is not None else None


async def save_cache_entry(
    session: AsyncSession,
    planfact_key_id: int,
    period_month: str,
    payload: dict,
    *,
    kind: str = CACHE_KIND_PLANFACT_PNL,
    frozen_by_user_id: Optional[int] = None,
) -> None:
    """Заморозить снэпшот за период. Если запись уже есть — перезаписываем
    (бывает после переоткрытия и повторного запроса)."""
    stmt = (
        pg_insert(CacheHistory)
        .values(
            planfact_key_id=planfact_key_id,
            kind=kind,
            period_month=period_month,
            payload=payload,
            frozen_by_user_id=frozen_by_user_id,
        )
        .on_conflict_do_update(
            index_elements=["planfact_key_id", "kind", "period_month"],
            set_={
                "payload": payload,
                "frozen_at": datetime.now(timezone.utc),
                "frozen_by_user_id": frozen_by_user_id,
            },
        )
    )
    await session.execute(stmt)


async def delete_cache_entry(
    session: AsyncSession,
    planfact_key_id: int,
    period_month: str,
    *,
    kind: str = CACHE_KIND_PLANFACT_PNL,
) -> bool:
    """Удалить снэпшот закрытого периода. Возвращает True если запись была.

    Используется кнопкой «Обновить» в дашборде, когда юзер хочет принудительно
    пересобрать данные за закрытый месяц (правки задним числом в PlanFact).
    При следующем /api/pnl build_pnl увидит cache miss → сходит в PF живьём и
    допишет свежий snapshot через save_cache_entry."""
    stmt = (
        delete(CacheHistory)
        .where(
            CacheHistory.planfact_key_id == planfact_key_id,
            CacheHistory.kind == kind,
            CacheHistory.period_month == period_month,
        )
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0


async def list_cache_entries(
    session: AsyncSession, planfact_key_id: int,
) -> list[dict]:
    """Список замороженных периодов для UI. Возвращаем без payload —
    он может быть тяжёлым."""
    stmt = (
        select(
            CacheHistory.kind,
            CacheHistory.period_month,
            CacheHistory.frozen_at,
            CacheHistory.frozen_by_user_id,
        )
        .where(CacheHistory.planfact_key_id == planfact_key_id)
        .order_by(CacheHistory.period_month.desc())
    )
    result = await session.execute(stmt)
    return [
        {
            "kind": r[0],
            "period_month": r[1],
            "frozen_at": r[2].isoformat() if r[2] else None,
            "frozen_by_user_id": r[3],
        }
        for r in result.all()
    ]


async def delete_cache_entry(
    session: AsyncSession,
    planfact_key_id: int,
    period_month: str,
    kind: str = CACHE_KIND_PLANFACT_PNL,
) -> None:
    """Переоткрытие месяца. Следующий запрос за этот период пойдёт live
    в PF и при необходимости перекэшируется."""
    await session.execute(
        delete(CacheHistory).where(
            CacheHistory.planfact_key_id == planfact_key_id,
            CacheHistory.kind == kind,
            CacheHistory.period_month == period_month,
        )
    )
