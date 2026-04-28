"""ORM-модели данных pnl-service.

Все таблицы — multi-tenant: owner_id BIGINT REFERENCES users(id) ON DELETE CASCADE.
Когда пользователя удаляют, все его данные уходят вместе.

Состав:
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
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


# ---------- Targets ----------

class Target(Base):
    """Per-project per-metric таргет в долях (0.05 = 5%)."""
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(32), nullable=False)
    target_pct: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "project_id", "metric_code",
                         name="uq_targets_owner_project_metric"),
        Index("ix_targets_owner_project", "owner_id", "project_id"),
    )


class DefaultTarget(Base):
    """Дефолтный таргет по метрике (если нет per-project override)."""
    __tablename__ = "default_targets"

    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    metric_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    target_pct: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )


# ---------- App settings (KV) ----------

class AppSetting(Base):
    """KV-настройки на пользователя: include_manager_in_lc, и т.д."""
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
    """Конфиг проекта: активность, имя, сортировка, привязка к Dodo IS unit."""
    __tablename__ = "projects_config"

    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    sort_order: Mapped[Optional[int]] = mapped_column(Integer)
    dodo_unit_uuid: Mapped[Optional[str]] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )


# ---------- Ops metrics ----------

class OpsMetric(Base):
    """Операционные метрики на месяц. PK (owner_id, project_id, period_month)."""
    __tablename__ = "ops_metrics"

    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    period_month: Mapped[str] = mapped_column(String(7), primary_key=True)
    orders_per_courier_h: Mapped[Optional[float]] = mapped_column(Float)
    products_per_h: Mapped[Optional[float]] = mapped_column(Float)
    revenue_per_person_h: Mapped[Optional[float]] = mapped_column(Float)
    late_delivery_certs: Mapped[Optional[int]] = mapped_column(Integer)
    delivery_orders_count: Mapped[Optional[int]] = mapped_column(Integer)
    late_delivery_certs_pct: Mapped[Optional[float]] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("ix_ops_metrics_owner_period", "owner_id", "period_month"),
    )


# ---------- Ops targets ----------

class OpsTarget(Base):
    """Дефолтный таргет ops-метрики."""
    __tablename__ = "ops_targets"

    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    metric_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )


class OpsProjectTarget(Base):
    """Override ops-таргета на конкретный проект."""
    __tablename__ = "ops_project_targets"

    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metric_code: Mapped[str] = mapped_column(String(32), primary_key=True)
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("ix_template_pfkey_path", "planfact_key_id", "path_lc"),
        Index("ix_template_pfkey_parent", "planfact_key_id", "parent_id"),
        Index("ix_template_pfkey_sort", "planfact_key_id", "sort_order"),
    )
