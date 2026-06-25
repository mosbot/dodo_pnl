"""ORM-модели данных pnl-service.

Все таблицы — multi-tenant: owner_id BIGINT REFERENCES users(id) ON DELETE CASCADE.
Когда пользователя удаляют, все его данные уходят вместе.

Состав:
- UserProjectVisibility — какие проекты видимы конкретному юзеру (per-user)
- Target               — таргеты UC/LC/DC/TC по проекту
- DefaultTarget        — глобальные дефолтные таргеты
- AppSetting           — key-value настройки на пользователя
- ProjectConfig        — активность/имя/сортировка проектов
- OpsMetric            — операционные метрики по месяцам
- OpsTarget            — глобальные таргеты ops-метрик
- OpsProjectTarget     — override ops-таргета по проекту
- PnLTemplateNode      — узлы шаблона P&L из импорта PlanFact

Все таблицы — в схеме pnl_service (через Base.metadata).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


# ---------- Targets ----------

class Target(Base):
    """Per-project per-metric таргет в долях (0.05 = 5%).

    Привязан к planfact_key, а не к юзеру: метрики (UC/LC/DC/...) теперь
    общие на ключ, таргеты по ним — тоже.

    S14.1: добавлен period_month в PK. '__default__' = «на все месяцы»,
    конкретный 'YYYY-MM' = override для этого месяца. Логика fallback —
    в store.effective_target() (monthly → default).
    """
    __tablename__ = "targets"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metric_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    period_month: Mapped[str] = mapped_column(
        String(16), primary_key=True, server_default=text("'__default__'")
    )
    target_pct: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("ix_targets_pfkey_project", "planfact_key_id", "project_id"),
    )


class DefaultTarget(Base):
    """Дефолтный таргет по метрике (если нет per-project override).

    S14.1: + period_month в PK; '__default__' = на все месяцы.
    """
    __tablename__ = "default_targets"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    metric_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    period_month: Mapped[str] = mapped_column(
        String(16), primary_key=True, server_default=text("'__default__'")
    )
    target_pct: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )


# ---------- App settings (KV) ----------

class AppSetting(Base):
    """KV-настройки на пользователя (произвольные ключи)."""
    __tablename__ = "app_settings"

    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )


# ---------- Projects config ----------

class ProjectConfig(Base):
    """Конфиг проекта: активность, имя, сортировка, привязка к Dodo IS unit.

    Привязан к planfact_key, а не к юзеру: проект — сущность аккаунта PlanFact.
    Все юзеры с одним ключом видят одинаковую настройку.
    """
    __tablename__ = "projects_config"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # S15: Whitelist для network_admin. Если False — проект «выключен на
    # уровне сети» супер-админом, network_admin не видит его в админке
    # и не может включить is_active. Юзеры тоже не видят такой проект
    # на дашборде. Для super_admin виден всегда.
    is_admin_managed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    sort_order: Mapped[Optional[int]] = mapped_column(Integer)
    dodo_unit_uuid: Mapped[Optional[str]] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )


# ---------- Cache history (immutable снэпшоты закрытых месяцев) ----------

class CacheHistory(Base):
    """Снэпшот агрегатов P&L PlanFact за закрытый месяц.

    Принципы:
      - Месяц «закрытый» если выпал из live-окна ключа (см.
        PlanfactKey.live_months_window). До этого — live из PF.
      - Один и тот же месяц для ключа кэшируется навсегда, пока админ
        явно не нажмёт «Переоткрыть» (DELETE по PK).
      - kind зарезервирован под расширение, сейчас всегда 'planfact_pnl'.
        payload — cat_totals + revenue_by_channel + active_project_ids,
        всё что нужно build_pnl чтобы собрать lines.

    Dodo IS-ops не кэшируем здесь: они уже хранятся в ops_metrics со
    своим pull-механизмом через /api/ops-metrics/sync.
    """
    __tablename__ = "cache_history"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    period_month: Mapped[str] = mapped_column(String(7), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )
    frozen_by_user_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    __table_args__ = (
        Index("ix_cache_history_pfkey_period", "planfact_key_id", "period_month"),
    )


# ---------- User project visibility (per-user override) ----------

class UserProjectVisibility(Base):
    """Какие проекты видны конкретному юзеру.

    По умолчанию все проекты видимы — записи создаются только когда админ
    выключает доступ. Полный фильтр на главной:
        projects_config.is_active     (общая «архивация» на ключ)
        AND user_project_visibility.is_visible
            (per-user скрытие; если записи нет → True)
    """
    __tablename__ = "user_project_visibility"

    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("ix_user_visibility_owner", "owner_id"),
    )


# ---------- Ops metrics ----------

class OpsMetric(Base):
    """Операционные метрики на месяц. PK (planfact_key_id, project_id, period_month).

    S11.6: с owner_id переехало на planfact_key_id — данные в Dodo IS не
    зависят от пользователя, поэтому делим один синк на всех юзеров ключа.
    """
    __tablename__ = "ops_metrics"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    period_month: Mapped[str] = mapped_column(String(7), primary_key=True)
    orders_per_courier_h: Mapped[Optional[float]] = mapped_column(Float)
    products_per_h: Mapped[Optional[float]] = mapped_column(Float)
    revenue_per_person_h: Mapped[Optional[float]] = mapped_column(Float)
    late_delivery_certs: Mapped[Optional[int]] = mapped_column(Integer)
    delivery_orders_count: Mapped[Optional[int]] = mapped_column(Integer)
    late_delivery_certs_pct: Mapped[Optional[float]] = mapped_column(Float)
    # S16: метрики из /delivery/statistics
    orders_per_trip: Mapped[Optional[float]] = mapped_column(Float)
    courier_utilization_pct: Mapped[Optional[float]] = mapped_column(Float)
    # S16.2: время храним в секундах (INT), на UI формат mm:ss
    avg_order_trip_time_sec: Mapped[Optional[int]] = mapped_column(Integer)
    # S18: среднее время доставки (avgDeliveryOrderFulfillmentTime), сек —
    # месячное историческое значение того же поля, что Пульс показывает live.
    avg_delivery_fulfillment_sec: Mapped[Optional[int]] = mapped_column(Integer)
    avg_cooking_time_delivery_sec: Mapped[Optional[int]] = mapped_column(Integer)
    avg_cooking_time_restaurant_sec: Mapped[Optional[int]] = mapped_column(Integer)
    # S16.3: расчётный KC% — net wage кухонных смен / выручка × 100
    kc_live_pct: Mapped[Optional[float]] = mapped_column(Float)
    # DC: расчётный Delivery Cost% — net wage курьерских смен (staffType ==
    # 'Courier') / выручка ДОСТАВКИ × 100. См. миграцию 0028.
    dc_live_pct: Mapped[Optional[float]] = mapped_column(Float)
    # Controlling API (0032): РКО — рейтинг клиентского опыта, РС — рейтинг
    # стандартов. rate 0..100 = среднее недельных рейтингов месяца из
    # history-эндпоинта (Calculated+Published). Заполняется для любого месяца.
    rko_rate: Mapped[Optional[int]] = mapped_column(Integer)
    rs_rate: Mapped[Optional[int]] = mapped_column(Integer)
    # Customer Rating API (0033): средняя оценка заказов 0..5 за месяц.
    # customer_rating — общее (взвешено по числу оценок зал+доставка);
    # _dinein / _delivery — по каналам отдельно (None если оценок канала нет).
    customer_rating: Mapped[Optional[float]] = mapped_column(Float)
    customer_rating_dinein: Mapped[Optional[float]] = mapped_column(Float)
    customer_rating_delivery: Mapped[Optional[float]] = mapped_column(Float)
    # Скользящее среднее (0034), период-независимое: РКО за последние 12 недель,
    # РС за последние 6 проверок. Пишется только при синке текущего месяца.
    rko_avg12w: Mapped[Optional[int]] = mapped_column(Integer)
    rs_avg6: Mapped[Optional[int]] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("ix_ops_metrics_pfkey_period", "planfact_key_id", "period_month"),
    )


# ---------- Monthly revenue history (S17 для /board prognoz) ----------

class MonthlyRevenueHistory(Base):
    """Снэпшот закрытых месяцев из Dodo IS для расчёта прогноза LFL.

    Закрытый месяц immutable — пишем один раз, дальше читаем из БД без
    обращений к Dodo IS. Используется как `last_year_full_month` в формуле
    прогноза `mtd × (LY_full / MTD_LFL)`.
    """
    __tablename__ = "monthly_revenue_history"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    revenue_total: Mapped[Optional[float]] = mapped_column(Float)
    revenue_delivery: Mapped[Optional[float]] = mapped_column(Float)
    revenue_restaurant: Mapped[Optional[float]] = mapped_column(Float)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()"),
    )

    __table_args__ = (
        Index(
            "ix_monthly_revenue_pfkey_month",
            "planfact_key_id", "month",
        ),
    )


# ---------- Dodo IS units cache (S18) ----------

class DodoisUnitCache(Base):
    """Кэш имён пиццерий из /auth/roles/units. Имя меняется крайне редко
    (новая точка — раз в месяцы), TTL ~24h. Без зависимости от тенанта —
    Кубинка-1 это Кубинка-1 для всех."""

    __tablename__ = "dodois_units_cache"

    uuid: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()"),
    )


# ---------- Board card metric visibility (S19) ----------

class BoardCardMetricVisibility(Base):
    """Per-PF-ключ настройка видимости ops-метрик в rich-card на /board.

    Запись отсутствует → метрика видна (default). UI на /settings
    выключает/включает метрики; backend фильтрует payload."""

    __tablename__ = "board_card_metric_visibility"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    metric_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()"),
    )


# ---------- Dodo IS baseline window cache (S21) ----------

class DodoisWindowCache(Base):
    """Кэш immutable baseline-окон /board (S21, #3 из бэклога).

    Окна сравнения last_week / mtd_lfl всегда заканчиваются в ПРОШЛОМ
    (now−7д / now−1год), округлены до часа — поэтому за конкретный
    `window_to_key` данные неизменны. Раньше тянулись из Dodo IS на
    каждую перегенерацию /board (board-кэш живёт 60с). Теперь — один раз
    на (ключ, проект, метрика, час), insert-only, переживает рестарт.

    metric_type: 'sales_lw' | 'monthly_lfl' (на первом этапе только
    критичные, честно бросающие при ошибке fetch'и; ops-метрики через
    _safe_fetch маскируют ошибку пустотой — их кэшировать опасно).
    window_to_key: ISO 'YYYY-MM-DDTHH:00:00' границы окна.
    payload: ответ endpoint'а для ОДНОГО юнита (JSONB).
    """

    __tablename__ = "dodois_window_cache"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metric_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    window_to_key: Mapped[str] = mapped_column(String(20), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()"),
    )


class LiteRevenueCache(Base):
    """Immutable-кэш выручки закрытых месяцев для Lite-режима (S19).

    В Lite (без PlanFact) выручка/каналы тянутся из Dodo IS. Для закрытого
    полного месяца значение неизменно — кэшируем по (ключ, проект, месяц),
    чтобы не дёргать Dodo IS на каждый просмотр истории (аналог cache_history
    в полном P&L, но с полной разбивкой по каналам). Текущий/частичный месяц
    не кэшируется (всегда live).

    payload = {"total": float, "channels": {delivery, restaurant, takeaway, other}}.
    """

    __tablename__ = "lite_revenue_cache"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()"),
    )


# ---------- Ops targets ----------

class OpsTarget(Base):
    """Дефолтный таргет ops-метрики на уровне PF-ключа.

    S14.1: + period_month в PK; '__default__' = на все месяцы.
    """
    __tablename__ = "ops_targets"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    metric_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    period_month: Mapped[str] = mapped_column(
        String(16), primary_key=True, server_default=text("'__default__'")
    )
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )


class OpsProjectTarget(Base):
    """Override ops-таргета на конкретный проект под PF-ключом.

    S14.1: + period_month в PK; '__default__' = на все месяцы.
    """
    __tablename__ = "ops_project_targets"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metric_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    period_month: Mapped[str] = mapped_column(
        String(16), primary_key=True, server_default=text("'__default__'")
    )
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )


# ---------- PnL template ----------

class PnLTemplateNode(Base):
    """Узел шаблона P&L. parent_id — self-FK на pnl_template.id.

    Привязан к planfact_key, а не к юзеру: один аккаунт PlanFact = одна
    структура. Юзеры с одним ключом делят шаблон. Write-доступ контролирует
    роутер (admin only), БД сама различает только владельца ключа.
    """
    __tablename__ = "pnl_template"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    # parent_id ссылается на pnl_service.pnl_template.id того же ключа.
    # FK не ставим — упрощает batch-replace при импорте шаблона (DELETE всё,
    # потом INSERT по порядку: иначе пришлось бы возиться с ON DELETE SET NULL).
    parent_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    path_lc: Mapped[str] = mapped_column(Text, nullable=False)
    is_calc: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_leaf: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    pnl_code: Mapped[Optional[str]] = mapped_column(String(32))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    # line_no — порядковый номер в иерархии (1..N в DFS-порядке шаблона
    # этого ключа). Используется в формулах метрик: `[14] / [7]`. При
    # повторном импорте шаблона стабильность сохраняем через path-match.
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("ix_template_pfkey_path", "planfact_key_id", "path_lc"),
        Index("ix_template_pfkey_parent", "planfact_key_id", "parent_id"),
        Index("ix_template_pfkey_sort", "planfact_key_id", "sort_order"),
    )


# ---------- P&L metrics (KPI с формулами) ----------

class PnLMetric(Base):
    """KPI шаблона P&L. Привязан к planfact_key, формула ссылается на
    line_no узлов pnl_template через `[N]`.

    Примеры формул:
      UC = `[13] / [7]`
      DC = `[20] / [9]`           — DC от выручки доставки, не общей
      TC = `([13]+[19]+[20]) / [7]`
      EBITDA = `[75]`
    """
    __tablename__ = "pnl_metrics"

    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    is_target: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    # pct | rub | x  — для форматирования в UI и при сравнении с таргетом
    format: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pct'"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    # Минимальный visibility_level юзера, при котором эта метрика видна.
    # 0 = видят все. См. User.visibility_level.
    min_visibility_level: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    # Рисовать ли плитку на карточке проекта (формат='rub' → фин-блок,
    # 'pct'/'x' → блок метрик). False прячет именно плитку, формула
    # продолжает считаться (нужно для drill'а / API). См. seed_metrics.
    is_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("ix_pnl_metrics_pfkey_sort", "planfact_key_id", "sort_order"),
    )
