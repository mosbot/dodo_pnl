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
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


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

    # PlanFact API key — храним у себя per-user (см. обсуждение архитектуры).
    # NULL = ключ ещё не настроен через UI «Интеграции».
    planfact_api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

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
