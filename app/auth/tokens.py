"""Token resolver — получить актуальные креды для текущего пользователя.

PlanFact API key:
    Хранится в pnl_service.users.planfact_api_key (записывается админом через
    UI «Интеграции» в S4.2 или через CLI). Постоянный, не OAuth.

Dodo IS access token:
    Хранится в public.dodois_credentials.access_token, обновляется кроном
    соседского сервиса каждые ~30 мин. Идентифицируется по
    public.dodois_credentials.name, имя привязывается к нашему юзеру через
    pnl_service.users.dodois_credentials_name.

env-fallback на общие токены (settings.planfact_api_key / dodo_is_access_token)
УБРАН (2026-06-26, security-hardening). Раньше при отсутствии своего ключа/привязки
код молча отдавал ОБЩИЙ ключ/токен — для Lite-тенанта это тихая утечка чужих данных
при первой же забытой is_lite-ветке. Теперь «нет данных тенанта» = явная ошибка
(NoTokenError → 4xx/5xx), а не подмена чужими кредами. На момент сноса ни один
прод-юзер на fallback не сидел (full-тенанты имеют свой api_key; SSO-юзеры идут в
брокер по dodois_sub; у Lite планфакт-ключа нет). env-переменные можно удалить из
.env — код их больше не читает.
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
    """PlanFact api key пользователя через каталог planfact_keys. Если у юзера
    не привязан ключ — env-fallback (для transition period). Бросает
    NoTokenError если совсем ничего нет.

    Расшифровывает api_key через app.crypto.decrypt_secret — legacy
    plain-значения проходят без изменений.
    """
    from ..crypto import decrypt_secret
    if user.planfact_key_id:
        from .models import PlanfactKey
        pk = await session.get(PlanfactKey, user.planfact_key_id)
        if pk and pk.api_key:
            decrypted = decrypt_secret(pk.api_key)
            if decrypted:
                return decrypted
    # env-fallback УБРАН: не отдаём общий PlanFact-ключ (тихая утечка). У Lite-
    # тенанта своего ключа нет — он идёт в _build_pnl_lite и сюда не заходит;
    # если зашёл (забытая is_lite-ветка) — пусть падает явно, а не утекает.
    raise NoTokenError(
        "Не настроен PlanFact API key. Lite-тенанту PlanFact не нужен; для "
        "полного P&L назначьте ключ через /settings → Пользователи."
    )


async def _fetch_token_from_broker(sub: str, name: str) -> str:
    """Валидный Dodo IS токен у токен-брокера sa (GET ?sub=, X-Admin-Token).

    sa тихо рефрешит токен по offline_access. Любая ошибка → NoTokenError
    (НЕ фолбэчимся на env, чтобы не отдать токен чужого аккаунта).
    """
    import httpx

    headers = {}
    if settings.sa_internal_token:
        headers["X-Admin-Token"] = settings.sa_internal_token
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                settings.sa_token_broker_url, params={"sub": sub}, headers=headers
            )
        resp.raise_for_status()
        token = (resp.json().get("access_token") or "").strip()
    except Exception as exc:  # noqa: BLE001 — любой сбой брокера = нет токена
        raise NoTokenError(
            f"Токен-брокер sa не отдал токен для {name!r} (sub {sub}): {exc}"
        ) from exc
    if not token:
        raise NoTokenError(f"Брокер sa вернул пустой токен для {name!r}.")
    return token


async def get_dodois_token(session: AsyncSession, user: User) -> str:
    """Dodo IS access token для пользователя.

    Источник по приоритету:
      1) Токен-брокер sa — если задан settings.sa_token_broker_url И имя есть
         в settings.dodois_sub_map (name → sub аккаунта в sa). Целевой путь.
      2) Legacy: public.dodois_credentials по name (старый VPS, где токены
         рефрешит соседский сервис «касса»). Используется, если брокер не
         настроен или для имени нет sub.
      3) env-fallback (settings.dodo_is_access_token) — ТОЛЬКО когда у юзера
         нет привязки (пустой name), чтобы не отдать токен чужого аккаунта.
    """
    # SSO-юзер: его Dodo sub известен напрямую → токен у брокера sa по sub
    # (без карты DODOIS_SUB_MAP — она для ручных/legacy юзеров).
    sso_sub = (getattr(user, "dodois_sub", None) or "").strip()
    if sso_sub and settings.sa_token_broker_url:
        return await _fetch_token_from_broker(sso_sub, sso_sub)

    name = (user.dodois_credentials_name or "").strip()
    if not name:
        # env-fallback УБРАН: общий Dodo-токен = чужой аккаунт → утечка ролей/
        # данных. Нет привязки (ни sub, ни name) → явная ошибка, не подмена.
        raise NoTokenError(
            "Не настроена привязка к Dodo IS. Войдите через Dodo IS (SSO) или "
            "пропишите имя из dodois_credentials в /settings → Интеграции."
        )

    # 1) Токен-брокер sa (целевой путь на SA-VPS).
    if settings.sa_token_broker_url:
        sub = settings.dodois_sub_map.get(name)
        if sub:
            return await _fetch_token_from_broker(sub, name)

    # 2) Legacy: читаем access_token из соседской таблицы. SQL — raw text,
    # потому что public.dodois_credentials не моделится у нас (read-only сосед).
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
