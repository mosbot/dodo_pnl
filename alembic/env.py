"""Alembic env.py — async-aware, с интеграцией наших настроек.

Особенности:
1. URL берём из app.config.settings.database_url (не из alembic.ini).
2. version_table в нашей схеме pnl_service, чтобы не засорять public.
3. include_schemas=True + include_object: видим только наши таблицы при
   autogenerate — соседская public.* игнорируется.
4. Async-режим через AsyncEngine.run_sync(...).
"""
from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Добавляем корень проекта в sys.path, чтобы импорты app.* работали
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402

# Импортируем все ORM-модели здесь — чтобы alembic-autogenerate их видел.
# При добавлении новой модели — приписать её импорт сюда.
from app.auth import models as _auth_models  # noqa: E402,F401
from app import models as _data_models  # noqa: E402,F401


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL — из settings, не из alembic.ini
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def include_object(object_, name, type_, reflected, compare_to):
    """autogenerate-фильтр: видим только нашу схему.

    Без этого alembic при autogenerate увидит соседские таблицы в public
    и попытается их «удалить» в миграции (т.к. их нет в Base.metadata).
    """
    if type_ == "table":
        # Считаем только таблицы из нашей схемы. object_.schema берётся из
        # MetaData(schema=...) для своих таблиц; для отражённых reflected
        # таблиц — из реальной схемы в БД.
        return object_.schema == settings.db_schema
    return True


def run_migrations_offline() -> None:
    """Offline-режим: генерация SQL без подключения к БД (alembic upgrade --sql)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=settings.db_schema,
        include_schemas=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=settings.db_schema,
        include_schemas=True,
        include_object=include_object,
        compare_type=True,             # реагировать на смену типов колонок
        compare_server_default=True,   # реагировать на смену default
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Online-режим: реальный коннект к БД через AsyncEngine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # alembic — короткоживущий процесс, pool не нужен
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
