"""Async SQLAlchemy 2.0 engine + session factory.

Backend: PostgreSQL 14+ via asyncpg. Все наши таблицы — в схеме pnl_service.
Соседская `public.dodois_credentials` доступна как read-only через тот же
коннект (мы делаем JOIN/SELECT в неё в S3 token_resolver).

Использование:
    # FastAPI:
    @app.get("/api/something")
    async def handler(session: AsyncSession = Depends(get_session)):
        result = await session.execute(select(Foo).where(...))
        ...

    # standalone (миграции, CLI):
    async with SessionLocal() as session:
        ...

Engine — singleton, ленивая инициализация: на старте уvicorn-воркера ничего не
происходит, первый запрос к БД создаёт connection pool.
"""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


# Все наши таблицы — в схеме pnl_service. MetaData(schema=...) применяет это
# глобально к Base.metadata: alembic-autogenerate тоже видит таблицы в нашей
# схеме, а не в public, и не пытается мигрировать соседские таблицы.
metadata_obj = MetaData(schema=settings.db_schema)


class Base(DeclarativeBase):
    """Базовый класс ORM-моделей. Все модели должны наследоваться от него."""
    metadata = metadata_obj


# Singleton'ы. Лениво инициализируются при первом обращении.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine() -> AsyncEngine:
    """Собрать AsyncEngine из settings.database_url.

    pool_pre_ping=True — проверяем коннект перед использованием (защита от
    «mysql server has gone away»-style разрывов после простоя).
    pool_recycle=3600 — пересоздаём коннекты раз в час, чтобы Postgres не
    закрывал их по idle_timeout.
    """
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL не задан. Положи в .env строку вида "
            "postgresql+asyncpg://pnl_user:pwd@127.0.0.1:5432/postgres"
        )
    return create_async_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        # echo=True для отладки SQL — в проде выключено
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _session_factory


# Удобный alias — для использования в скриптах/CLI без вызова get_*()
SessionLocal = get_session_factory  # type: ignore[assignment]


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-dependency.

    На каждый запрос — новая сессия. Коммит при успешном выходе, rollback при
    исключении. Не используем `expire_on_commit=False` на уровне сессии —
    объекты остаются доступными после коммита (нужно для возврата JSON).
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping() -> dict[str, str]:
    """Health-check: SELECT 1 — проверяет, что engine и pool работают.

    Используется в /api/health. Возвращает dict с DB-версией, чтобы при
    проблемах было видно, на какой Postgres мы ходим.
    """
    from sqlalchemy import text
    async with get_engine().connect() as conn:
        result = await conn.execute(text("SELECT version()"))
        version = result.scalar_one()
        return {"db": "ok", "version": str(version)[:80]}
