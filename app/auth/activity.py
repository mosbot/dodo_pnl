"""Дневной лог активности пользователей (user_activity_days).

Зачем: sessions.last_seen_at хранит только последний момент активности
долгоживущей сессии (TTL 30 дней) — история заходов между created_at и
last_seen невидима. Эта таблица даёт честные «дни активности».

Механика: in-memory аккумулятор per (user_id, день MSK). На каждый
авторизованный запрос инкрементим pending; в БД пишем upsert'ом не чаще
раза в _FLUSH_INTERVAL на пользователя (первый хит дня — сразу, чтобы день
появился мгновенно). Любая ошибка глотается — активность никогда не должна
ломать аутентификацию.

Погрешности (осознанные): при рестарте контейнера теряется ≤10 мин
незаписанных инкрементов; requests — приблизительный счётчик, не access-log.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_MSK = timezone(timedelta(hours=3))
_FLUSH_INTERVAL = 600.0  # сек между записями в БД на пользователя

# (user_id, 'YYYY-MM-DD') -> [pending_requests, last_flush_monotonic]
_acc: dict[tuple[int, str], list[float]] = {}

_UPSERT = text("""
    INSERT INTO pnl_service.user_activity_days (user_id, day, requests)
    VALUES (:uid, :day, :n)
    ON CONFLICT (user_id, day) DO UPDATE
    SET requests     = pnl_service.user_activity_days.requests + EXCLUDED.requests,
        last_seen_at = now()
""")


async def note_activity(db: AsyncSession, user_id: int) -> None:
    """Отметить авторизованный запрос пользователя. Дёшево; сама пишет в БД
    с дебаунсом. Вызывается из auth-зависимости на каждый запрос."""
    try:
        now_mono = time.monotonic()
        day_date = datetime.now(_MSK).date()
        day = day_date.isoformat()
        key = (user_id, day)
        st = _acc.get(key)
        if st is None:
            st = [0.0, 0.0]  # pending, last_flush (0 => флашим сразу)
            _acc[key] = st
        st[0] += 1
        if st[1] and now_mono - st[1] < _FLUSH_INTERVAL:
            return
        # flush этого ключа. SAVEPOINT: сбой upsert'а (например, окно деплоя
        # до миграции) не должен abort'ить транзакцию всего запроса.
        n = int(st[0])
        st[0] = 0.0
        st[1] = now_mono
        async with db.begin_nested():
            await db.execute(
                _UPSERT, {"uid": user_id, "day": day_date, "n": n}
            )
        # заодно доливаем и чистим ключи прошлых дней (rollover полуночи)
        stale = [k for k in _acc if k[1] != day]
        for k in stale:
            pend = int(_acc[k][0])
            if pend > 0:
                async with db.begin_nested():
                    await db.execute(
                        _UPSERT,
                        {"uid": k[0], "day": date.fromisoformat(k[1]),
                         "n": pend},
                    )
            del _acc[k]
    except Exception:  # noqa: BLE001 — активность не должна ломать auth
        log.warning("user activity upsert failed", exc_info=True)
