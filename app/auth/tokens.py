"""Token resolver — получить актуальные креды для текущего пользователя.

PlanFact API key:
    Хранится в pnl_service.users.planfact_api_key (записывается админом через
    UI «Интеграции» в S4.2 или через CLI). Постоянный, не OAuth.

Dodo IS access token:
    Хранится в public.dodois_credentials.access_token, обновляется кроном
    соседского сервиса каждые ~30 мин. Идентифицируется по
    public.dodois_credentials.name, имя привязывается к нашему юзеру через
    pnl_service.users.dodois_credentials_name.

Fallback на env-токены (settings.planfact_api_key / settings.dodo_is_access_token)
оставлен для переходного периода: пока andrey не настроил свой ключ через
UI/CLI, его дашборд продолжает работать на общих env-кредах. После того как
у юзера прописан свой ключ — fallback не срабатывает.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.models import User
from ..config import settings


class NoTokenError(Exception):
    """Нет ни своего ключа, ни env-fallback. UI/API должны вернуть 4xx."""


async def get_planfact_key(session: AsyncSession, user: User) -> str:
    """PlanFact api key пользователя или env-fallback. Бросает NoTokenError."""
    key = (user.planfact_api_key or "").strip()
    if key:
        return key
    fallback = (settings.planfact_api_key or "").strip()
    if fallback:
        return fallback
    raise NoTokenError(
        "Не настроен PlanFact API key. Зайди в /settings → Интеграции и "
        "пропиши свой ключ (или попроси администратора)."
    )


async def get_dodois_token(session: AsyncSession, user: User) -> str:
    """Dodo IS access token из public.dodois_credentials по user.dodois_credentials_name.

    Если у юзера привязки нет → пробуем env-fallback. Если access_token в
    соседской таблице null/пустой (соседский cron не успел/упал) — это уже
    ошибка, не молчим, не fallback'имся (по той причине, что у нас могут
    быть свежие env-токены, которые вообще не для этого юзера).
    """
    name = (user.dodois_credentials_name or "").strip()
    if not name:
        fallback = (settings.dodo_is_access_token or "").strip()
        if fallback:
            return fallback
        raise NoTokenError(
            "Не настроена привязка к Dodo IS. В /settings → Интеграции "
            "пропиши имя из dodois_credentials (логин Dodo IS)."
        )

    # Читаем access_token из соседской таблицы. SQL — raw text, потому что
    # public.dodois_credentials не моделится у нас (read-only ресурс соседа).
    stmt = text(
        "SELECT access_token FROM public.dodois_credentials WHERE name = :name"
    )
    result = await session.execute(stmt, {"name": name})
    row = result.first()
    if row is None:
        raise NoTokenError(
            f"В public.dodois_credentials нет строки с name={name!r}. "
            "Проверь, что имя пользователя Dodo IS указано без опечаток."
        )
    token = (row[0] or "").strip()
    if not token:
        raise NoTokenError(
            f"У {name!r} в dodois_credentials пустой access_token. "
            "Соседский сервис обновляет токены кроном — зайди через 5–10 мин "
            "или сообщи об инциденте администратору."
        )
    return token
