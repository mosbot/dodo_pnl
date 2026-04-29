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
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

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

    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

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
