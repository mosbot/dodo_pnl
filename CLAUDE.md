# pnl-service — контекст для Claude Code

Многотенантный SaaS P&L Dashboard для франчайзи Dodo Pizza. FastAPI +
SQLAlchemy 2.0 async + asyncpg + PostgreSQL. Прод-инсталляция —
`pnl.dodotool.ru` на VPS `dodotool.ru`.

## Repo layout

```
app/
  main.py              — FastAPI app + endpoints
  board.py             — /api/board orchestrator (сетевой scoreboard дня)
  day_window.py        — расчёт временных окон MSK для /board
  dodois_client.py     — клиент Dodo IS API (httpx, retry с jitter, rate-limit retry)
  models.py            — SQLAlchemy 2.0 models
  store.py             — DB-операции (get/upsert helpers)
  auth/                — token management, sessions
  config.py            — settings (pydantic BaseSettings)
  ...
alembic/versions/      — миграции, идут sequentially 0001 → 0022
static/
  board.html, board.js — рендер /board (compact + rich view-switch)
  *-mock.html          — статические мокапы для итераций дизайна
docs/
  dodois-api.md        — снапшот Dodo IS API схем
  planfact-agent-kit/  — внешняя дока PlanFact
```

## Прод

- VPS: `claude@dodotool.ru`, проект в `/home/claude/pnl-service/`
- Service: `pnl-uvicorn.service` (systemd). Рестарт: `sudo systemctl restart pnl-uvicorn`
- DB: PostgreSQL local, схема `pnl_service`, соседняя `public` содержит
  `dodois_credentials` (read-only mapping имя→access_token, refresh OAuth
  делает соседний сервис).
- Креды: `/home/claude/pnl-service/.env`
- nginx → uvicorn 127.0.0.1:5759

**Деплой code**: `scp app/*.py claude@dodotool.ru:/home/claude/pnl-service/app/`
**Деплой migrations**: `scp alembic/versions/*.py ...` + `ssh ... '.venv/bin/alembic upgrade head'`
**Деплой статики**: `scp static/* ... ; (без рестарта, версионируется ?v=N)`

## Aлембик / DB

Migrations 0001–0022. Последние:
- `0019` — KC_LIVE колонки в ops_metrics
- `0021` — `monthly_revenue_history` (immutable cache закрытых месяцев для прогноза)
- `0022` — `dodois_units_cache` (имена пиццерий, TTL 24h)

Локально:
```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic revision -m "S19: …" --rev-id 0023
```

## OAuth scopes (Dodo IS, ask530 token)

Активные scopes:
- `accounting` — sales, write-offs
- `production` — productivity, stop-sales-*
- `delivery` — statistics, vouchers, stop-sales-sectors, couriers-orders
- `finance` — sales/units/monthly
- `incentives` — staff/incentives-by-members (для KC_LIVE)
- `stopsales` — stop-sales-* (production + delivery)
- `staffshifts:read` — couriers-on-shift (добавлен но потом откатили курьеров)

Если падает 403 `InsufficientScopes` — нужен re-consent с новым scope.

## /board — сетевой scoreboard дня

**Назначение**: для territorial/network админа (visibility_level ≥ 30).
Показывает все пиццерии сети с дельтой выручки vs прошл. соответствующий
день недели, активные стопы, ops-метрики (Кухня + Доставка), месяц LFL,
прогноз LFL.

**Окна** (`day_window.py`):
- `today` — `00:00 MSK → now (с минутами, sec=0)`
- `last_week` — `LW дата 00:00 → nearest hour (≤:30 floor, >:30 ceil)`
- `mtd` — `1 число → now`
- `mtd_lfl` — same range, −1 год
- `last_year_full_month` — полный прошлогодний месяц (immutable, DB-cache)

**Источники Dodo IS** (на каждый /api/board, cache 60с per planfact_key+hour):
| Endpoint | Зачем | Где в payload |
|---|---|---|
| `/auth/roles/units` | Имена пиццерий | DB-cache `dodois_units_cache` (TTL 24h) |
| `/accounting/sales` (today, LW) | Channels split + минутная точность | `day.value/baseline/channels` |
| `/finances/sales/units/monthly` (MTD, MTD_LFL) | Месячные агрегаты | `month.value/baseline` |
| `/production/stop-sales-channels` | Стопы каналов | `stops.channels[]` |
| `/delivery/stop-sales-sectors` | Стопы секторов | `stops.sectors[]` |
| `/production/stop-sales-products` | Стопы продуктов | `stops.products[]` |
| `/production/stop-sales-ingredients` | Стопы ингр. | `stops.ingredients[]` |
| `/production/productivity` (today, LW) | ₽/чел·ч, шт/чел·ч | `ops.kitchen.*_per_hour` |
| `/delivery/statistics` (today, LW) | Готовка/доставка/полка | `ops.kitchen.cooking_time_sec`, `ops.kitchen.heated_shelf_sec`, `ops.delivery.avg_delivery_sec` |
| `/delivery/vouchers` (today, LW) | Сертификаты | `ops.delivery.vouchers_count` |

**Frontend** (`static/board.html` + `board.js`):
- Auto-refresh ОТКЛЮЧЁН (после rate-limit incidents). Hard reload only.
- View-switch: «Сводка» (compact table-card) / «Подробно» (rich card),
  `localStorage.boardView` persist.
- Sort 4 modes (day-Δ ↑↓, выручка↓, А→Я), localStorage.boardSort persist.
- Rich card: имя + Δ-пилл + month-mini + выручка + inline channels +
  crit-alert (раскрывающийся) + ops-grid (Кухня / Доставка).

## Известные подводные камни

### Rate limit / connection drops
- Dodo IS режет TCP-соединения при ~144+ parallel запросов с одного токена.
  `_MAX_PARALLEL=6` в `dodois_client.py` (опускали до 1 в incident; вернули
  обратно после удаления handover-dinein).
- 429 с `Try again in N seconds` — теперь retriable через `DodoISRateLimit`
  (см. `_with_retries`).
- При timeout / 403 на non-critical endpoint'ы — graceful degrade через
  `_safe_fetch_stops` / `_safe_fetch_ops` (возвращают `[]` или `{}`).
  Критические (sales, monthly) — НЕ wrap'аны, чтобы 502 кричал явно.

### Готовка зал отключена
Откатили `/production/orders-handover-statistics` с `salesChannels=DineIn`
— перегружало Dodo IS. План: вернуть через batched call (1 запрос на 30
юнитов) и DB-cache для immutable LW значений.

### Курьеры на смене / в очереди — НЕ ИСПОЛЬЗУЕМ
`/staff/couriers-on-shift` работает, но Dodo IS UI «в очереди»
вычисляется через real-time stream который не доступен в публичном API.
Approximation через `numberOfCouriersInQueue` из couriers-orders =
устаревший snapshot. Решили вообще не показывать.

### Имена пиццерий зависели от продаж
Был баг: имя извлекалось из `accounting/sales.unitName` — если за день
ещё нет продаж (раннее утро), имя = `project_id`. Фикс: 
`/auth/roles/units` → DB-cache. См. `_get_or_refresh_unit_names`.

### iOS Safari auto-link длинных чисел как tel:
`<meta name="format-detection">` + `a[href^="tel:"]{pointer-events:none}` в HTML.

### Не коммитить .py в static/
`SafeStaticFiles` + .gitignore guard. Был security incident
(`docs/audits/static-leak-incident.md`).

## Открытые задачи (приоритет)

### #2 — DB-cache для LW/MTD_LFL метрик (ЧАСТИЧНО СДЕЛАНО, S21)
Все «прошлые» значения immutable. Сейчас тянем каждые 60c.
Таблица `dodois_window_cache(planfact_key_id, project_id, metric_type,
window_to_key, payload JSONB, computed_at)` PK всё это, insert-only
(миграция 0026). Хелперы `store.get_window_cache_many` /
`upsert_window_cache`, интеграция в `board.build_board_payload` (читаем
кэш ДО gather, fetch только missing, пишем ПОСЛЕ — session нельзя
трогать конкурентно в gather). Проверка: `scripts/verify_board_window_cache.py`
(на хите baseline-фетчи 2→0, числа идентичны).

Закэшировано (✅):
- `sales_lw` (channels split + total за прошл. неделю до часа)
- `monthly_lfl` (LFL месяц до текущего дня/часа в прошлом году)

ОСТАЛОСЬ (ops-метрики — идут через `_safe_fetch_ops`, который маскирует
ошибку пустотой; кэшировать опасно без различения «успех но пусто» vs
«timeout/403». Нужен отдельный success-флаг от fetch перед записью):
- `productivity_lw` (₽/ч, шт/ч), `delivery_stats_lw`, `vouchers_count_lw`,
  `handover_lw` (готовка по каналам).
`monthly_lfl_full` уже в `monthly_revenue_history` (get_or_fetch_ly_full_month).

Реализация ops-кэша: обернуть fetch так, чтобы отличать пустой-успех от
ошибки (например fetch возвращает `(data, ok: bool)`), и писать в кэш
только при ok=True.

### #3 — Stops shared между users одного PF-ключа
Сейчас `_BOARD_CACHE` per-user, при concurrent users делает 4×N
лишних запросов. Сделать stops-cache на (planfact_key_id, hour).
TTL: 60-90с.

### Готовка зал (вернуть)
После #2 — кешируем `handover-statistics(DineIn)_lw` immutable.
Today тянем 1 раз за час (можно тоже cache до конца часа).
Total: 2 запроса вместо текущих 2×N.

### Stop-sectors timeout
`/delivery/stop-sales-sectors` стабильно timeout'ит в 8с. Endpoint
медленнее остальных. Решения: повысить budget или batch-вариант.

## Аккаунты для тестов

- `andrey@dodotool.ru` (network admin, planfact_key привязан к ask530 dodois)
  Пароль — у Андрея. Временный пароль из этого файла удалён 2026-06-10
  (секреты в репо не храним); если не был сменён — сменить через UI.

## Связанные docs

- `docs/dodois-api.md` — полный snapshot Dodo IS API (auto-gen scripts/pull_dodois_docs.py)
- `docs/planfact-agent-kit/` — внешняя дока PlanFact (для понимания target P&L)
- `docs/audits/*.md` — incident reports / audit notes

## Полезные команды

```bash
# Probe одного endpoint'а
.venv/bin/python /tmp/probe_<smth>.py

# Restart prod
ssh claude@dodotool.ru 'sudo systemctl restart pnl-uvicorn'

# Свежие логи
ssh claude@dodotool.ru 'sudo journalctl -u pnl-uvicorn -n 50 --no-pager'

# DB shell
ssh claude@dodotool.ru 'PGPASSWORD=... psql -h 127.0.0.1 -U pnl_user -d postgres'
```
