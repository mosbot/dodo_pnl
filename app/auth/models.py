"""ORM-модели User и Session.

User — наш пользователь pnl-service. dodois_credentials_name указывает на
public.dodois_credentials.name (read-only, у соседа); по нему мы достаём
свежий access_token при каждом запросе к Dodo IS.

Session — серверная сессия с токеном-кукой. Token = 32-байтный hex-randomtoken,
expires_at — точный момент истечения. Идём в БД на каждый запрос (Postgres
на localhost — это микросекунды, кэш не нужен).

Обе таблицы в схеме pnl_service (через Base.metadata.schema).
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
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


class PlanfactKey(Base):
    """Каталог именованных PlanFact API ключей. Пользователи ссылаются на
    него через users.planfact_key_id. Один ключ может использоваться
    несколькими юзерами (например, общий аккаунт компании)."""
    __tablename__ = "planfact_keys"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Глубина live-окна (в месяцах). Текущий + N-1 предыдущих месяцев
    # всегда читаются live из PF. Более старые — из cache_history.
    live_months_window: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default=text("2"),
    )
    # Источник P&L-агрегата (миграция S20 на v2 reports/opu):
    #   'raw'    — GET /operations + ручная агрегация (legacy, default)
    #   'shadow' — отвечает raw, параллельно в фоне сверяется v2 (лог дельт)
    #   'v2'     — POST /api/v2/reports/opu (fallback на raw при ошибке)
    pnl_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="raw", server_default=text("'raw'"),
    )
    # S22: для ТЕКУЩЕГО (live) полного месяца брать REVENUE и разбивку по
    # каналам из Dodo IS (/finances/sales/units/monthly) вместо PlanFact —
    # PlanFact подтягивает продажи дня лишь к ~23:15. Закрытые месяцы всегда
    # из PlanFact. Сбой Dodo → graceful fallback на PlanFact. Default FALSE.
    live_revenue_from_dodois: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    # DC: налоговые коэффициенты для расчётных KC%/DC% (множитель «налоги»).
    # Применяются на чтении ops-метрик (raw в БД, ×коэф. на отдаче). Default
    # 1.0 → KC не меняется, пока не задано. dc_live_enabled — показывать ли
    # расчётный DC (default FALSE). См. миграцию 0028.
    kc_tax_coefficient: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default=text("1.0"),
    )
    dc_tax_coefficient: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default=text("1.0"),
    )
    dc_live_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<PlanfactKey id={self.id} name={self.name!r}>"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # NULL для SSO-юзеров (вход через Dodo IS, без локального пароля).
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # SSO: стабильный sub Dodo-аккаунта (из sa). NULL у локальных юзеров.
    # Уникален — один pnl-юзер на Dodo-аккаунт. По нему резолвим SSO-вход
    # и берём Dodo-токен у брокера sa напрямую (см. auth/tokens).
    dodois_sub: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )

    # Имя в соседской таблице public.dodois_credentials, по которому
    # резолвим access_token. Может быть пустым, если юзер пока не привязан.
    dodois_credentials_name: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )

    # FK на запись в каталоге planfact_keys. NULL = ключ не назначен.
    # Сам api_key больше не хранится в users — лежит в planfact_keys.api_key
    # и доступен через JOIN. Меняется админом централизованно (одно место —
    # сразу всем юзерам, кому привязано).
    planfact_key_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="SET NULL"),
        nullable=True,
    )

    # S15: разделили админа на два уровня:
    #   super_admin   — Anthropic-уровень владения, видит/правит все ключи,
    #                   создаёт PF-ключи и сетевых админов (Andrey)
    #   network_admin — администратор одной сети, scope = его planfact_key,
    #                   создаёт юзеров и управляет проектами в whitelist'е,
    #                   который выдал super_admin (oltruist → Xfood)
    #   user          — обычный пользователь (Управляющий/Территориальный/...)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user", server_default=text("'user'"),
    )

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    @property
    def is_network_admin(self) -> bool:
        return self.role == "network_admin"

    @property
    def is_any_admin(self) -> bool:
        return self.role in ("super_admin", "network_admin")

    # Legacy alias — всё ещё используется в старых call sites (cli.py,
    # users.py:create_user, и т.д.). После прохода всех вызовов будет удалён.
    @property
    def is_admin(self) -> bool:
        return self.is_any_admin

    # Уровень видимости метрик P&L. Юзер видит метрику если его level
    # >= metric.min_visibility_level. Дефолтные пресеты:
    #   10 — Управляющий пиццерией
    #   30 — Территориальный управляющий
    #   60 — Директор
    #   100 — Партнёр (всё)
    visibility_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} admin={self.is_admin}>"


class UserSession(Base):
    """Серверная сессия. Имя класса с префиксом, чтобы не конфликтовать с
    SQLAlchemy `Session` (фабрика). Таблица — `sessions`."""

    __tablename__ = "sessions"

    # Token — primary key, тот же, что в cookie. 32 random bytes → 64 hex-char.
    token: Mapped[str] = mapped_column(String(128), primary_key=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(INET, nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")

    def __repr__(self) -> str:
        return f"<UserSession token={self.token[:8]}... user_id={self.user_id}>"


class AuditLog(Base):
    """Журнал security-событий. user_id NULL для login_failed (юзера не нашли).

    action — короткий код (login_success, login_failed, login_rate_limited,
    logout, password_changed, integrations_changed, admin_user_created,
    admin_user_deleted, admin_user_updated, admin_password_reset).
    details — произвольный JSONB (никогда не кладём пароли/токены, только
    метаданные: имена, флаги, target user_id и т.п.).

    ON DELETE SET NULL у user_id — не теряем историю, когда юзер удалён;
    запись остаётся для аудита.
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
        index=True,
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} action={self.action!r} user_id={self.user_id}>"


class AccessRequest(Base):
    """Запрос доступа от Dodo IS-пользователя без локального аккаунта.

    Пользователь вошёл через Dodo IS (SSO), но `dodois_sub` не привязан ни к
    одному pnl-юзеру, а тенант его сети уже существует. Он жмёт «Запросить
    доступ» → создаётся pending-запрос для `planfact_key_id` этой сети. Сетевой
    админ одобряет (выбирает уровень видимости) → создаётся User с привязкой
    `dodois_sub`, запрос → approved. `dodois_sub` берётся ТОЛЬКО из валидной
    sa-сессии (не из формы). Один pending на (planfact_key, dodois_sub).
    """
    __tablename__ = "access_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    planfact_key_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("planfact_keys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dodois_sub: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Снимок заведений запросившего (uuid+имя) — чтобы админ видел, кто просит.
    units: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    # pending | approved | denied
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )
    decided_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AccessRequest id={self.id} pf={self.planfact_key_id} "
            f"sub={self.dodois_sub[:8]} status={self.status!r}>"
        )
