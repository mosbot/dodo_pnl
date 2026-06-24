# pnl-service — контекст для Claude Code

Многотенантный SaaS P&L Dashboard для франчайзи Dodo Pizza. Модуль «Финансы»
(P&L) + «Пульс» (/board) зонтичной платформы **Dodotool**. FastAPI +
SQLAlchemy 2.0 async + asyncpg + PostgreSQL. Прод — `pnl.dodotool.ru`,
Docker-контейнер на SA-VPS `94.26.246.138` (там же сервис авторизации `sa`).

> Быстрый старт (деплой одной командой):
> ```bash
> ssh ask@94.26.246.138 'cd ~/pnl-service && ./scripts/deploy.sh "feat: …"'
> ```
> Логи: `ssh ask@94.26.246.138 'sudo docker logs -n 50 dodotool-pnl-api-1'`.
> Подробности — раздел «Прод (SA-VPS, Docker)».

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
alembic/versions/      — миграции, идут sequentially 0001 → 0027
static/
  board.html, board.js — рендер /board (compact + rich view-switch)
  *-mock.html          — статические мокапы для итераций дизайна
docs/
  dodois-api.md        — снапшот Dodo IS API схем
  planfact-agent-kit/  — внешняя дока PlanFact
scripts/deploy.sh      — деплой на SA-VPS (Docker; см. «Прод»)
Dockerfile             — образ pnl (python:3.11-slim + uvicorn)
docker-compose.pnl.yml — сервис pnl в общей docker-сети sa (Caddy + Postgres)
.dockerignore
```

## Прод (SA-VPS, Docker)

- VPS: `ask@94.26.246.138` — тот же сервер, что `sa` (репо `dodotool_sa_backend`,
  `~/dodotool-sa`). Домен `pnl.dodotool.ru`, TLS — Caddy sa (авто Let's Encrypt).
- Каталог = git-чекаут: `~/pnl-service` (`mosbot/dodo_pnl`, ветка `main`).
- Запуск: Docker-контейнер `dodotool-pnl-api-1` (uvicorn :8000) в общей
  docker-сети `dodotool-sa_default`. Compose: `docker-compose.pnl.yml`.
- Reverse-proxy: Caddy sa (`~/dodotool-sa/Caddyfile`, vhost
  `pnl.dodotool.ru → dodotool-pnl-api-1:8000`).
- DB: PostgreSQL в контейнере `dodotool-sa-postgres-1`, **отдельная БД
  `pnl_service`**, схема `pnl_service`. Из контейнера:
  `postgresql+asyncpg://dodotool_sa:***@postgres:5432/pnl_service`.
  (БД `dodois_credentials` принадлежит sa — это `dodotool_sa`, к ней pnl НЕ ходит.)
- Креды: `~/pnl-service/.env` (gitignore). Важное: `SECRET_KEY` (Fernet для
  PlanFact-ключей — **НЕ менять**, иначе ключи не расшифровать), `DATABASE_URL`,
  `SA_TOKEN_BROKER_URL`, `SA_INTERNAL_TOKEN`, `DODOIS_SUB_MAP`.

### Dodo IS токен — через брокер sa (НЕ из dodois_credentials напрямую)

pnl больше не читает `public.dodois_credentials`. Токен берётся у sa:
`GET http://api:8000/internal/dodois-token?sub=<sub>` (заголовок
`X-Admin-Token` = `SA_INTERNAL_TOKEN`); sa тихо рефрешит по offline_access.
Резолвер `app/auth/tokens.get_dodois_token` — **гибрид**: брокер sa (если задан
`SA_TOKEN_BROKER_URL` и имя в `DODOIS_SUB_MAP`) → legacy `dodois_credentials` →
env. `DODOIS_SUB_MAP` — JSON `{"<dodois_credentials_name>": "<sub в sa>"}`
(сейчас: `ask530`→Коваль Андрей, `loderan2`→Agent Dodotool). Брокер в sa —
`app/routers/internal.py`.

### Деплой (Docker, `scripts/deploy.sh`)

git-чекаут на VPS, remote `origin` = `git@github-dodo:mosbot/dodo_pnl.git`
(deploy key `~/.ssh/dodo_pnl_deploy`, alias `github-dodo` в `~/.ssh/config`;
ключ sa `dodotool_sa_backend` — отдельный, через `git@github.com`).

Правки делаешь прямо в чекауте (`ssh` + редактор) или scp в него, затем:
```bash
ssh ask@94.26.246.138 'cd ~/pnl-service && ./scripts/deploy.sh "feat: описание"'
```
deploy.sh атомарно: `git commit` (если есть правки) → `git pull --ff-only` →
`git push origin main` → `docker compose -f docker-compose.pnl.yml up -d --build`
→ `alembic upgrade head` (в контейнере) → health-check `/api/health`.
**При провале health-check — автооткат кода** (`git reset --hard` + rebuild).

Откат вручную:
```bash
ssh ask@94.26.246.138 'cd ~/pnl-service && git reset --hard <sha> && \
  sudo docker compose -f docker-compose.pnl.yml up -d --build'
```
NB: автооткат возвращает только КОД; миграцию БД откатывать вручную
(`docker exec dodotool-pnl-api-1 alembic downgrade <rev>`). Статику кэш-бастим
`?v=N` в html (отдельного шага нет).

### Тестовый контур (завести, когда появятся реальные юзеры)

Сейчас контур один (прод). Рекомендуемая схема staging на том же VPS, чтобы
проверять перед прод-деплоем:
- ветка `staging` в репо + отдельный чекаут `~/pnl-staging`;
- свой контейнер `dodotool-pnl-staging-1` (тот же Dockerfile, отдельный
  `docker-compose.staging.yml`, project `pnl-staging`) в сети `dodotool-sa_default`;
- отдельная БД `pnl_service_staging` (дамп прода → restore), свой `.env`
  (тот же `SECRET_KEY`, если нужны те же PlanFact-ключи);
- Caddy-vhost `staging.pnl.dodotool.ru → dodotool-pnl-staging-1:8000`;
- деплой staging из ветки `staging`, прод — только из `main` после проверки.
Завести по запросу (≈30 мин работы).

### Старый VPS (выводится)

`claude@dodotool.ru` (bare-metal `pnl-uvicorn`/systemd) — pnl остановлен и снят
с автозапуска. Его Caddy (`/home/fintool/Caddyfile`) **временно проксирует**
`pnl.dodotool.ru` на новый VPS (мост на период распространения DNS). После
полного перехода DNS — убрать мост и вывести сервер.

## Aлембик / DB

Migrations 0001–0027. Последние:
- `0019` — KC_LIVE колонки в ops_metrics
- `0021` — `monthly_revenue_history` (immutable cache закрытых месяцев для прогноза)
- `0022` — `dodois_units_cache` (имена пиццерий, TTL 24h)
- `0025` — `planfact_keys.pnl_source` (raw|shadow|v2)
- `0026` — `dodois_window_cache` (immutable baseline-окна /board)
- `0027` — `planfact_keys.live_revenue_from_dodois` (S22, см. ниже)

Локально:
```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic revision -m "S19: …" --rev-id 0023
```

## S22 — live-выручка текущего месяца из Dodo IS

Флаг `planfact_keys.live_revenue_from_dodois` (default FALSE; включён для
PiX=1, Xfood=3). Когда TRUE, для ТЕКУЩЕГО (live, незакрытого) полного месяца
строка REVENUE и разбивка по каналам берутся из Dodo IS
(`/finances/sales/units/monthly`, 1 батч-запрос ≤30 юнитов, ~1.5с холодный),
а не из PlanFact. Причина: PF подтягивает продажи дня лишь к ~23:15 + ловит
артефакты разнесения («Нераспределенный доход»). Закрытые месяцы и частичные
диапазоны — всегда PlanFact.

Реализация: `main._maybe_override_revenue_from_dodois` (вызов в
`_build_pnl_v2_result` перед `build_pnl`, только при `cache_mode=="off"` и
`period_month == текущий`). **Слой инъекции — cat_totals, а не totals**:
строка REVENUE считается `pnl._apply_metric_formulas` из шаблона ПланФакт,
который строится из cat_totals; totals[(pid,'REVENUE')] это лишь знаменатель.
Канал Dodo (Delivery/Dine-in/Takeaway) → revenue-категория с тем же
`revenue_channel`; «прочие» revenue-категории зануляются. totals и
revenue_by_channel выставляются согласованно. build_pnl пересчитывает все
pct_of_revenue / прибыль консистентно. Сбой Dodo / любая ошибка → graceful
fallback на выручку PlanFact (страница не ломается). Работает только на
v2-пути; raw-fallback отдаёт PF-выручку. Kill-switch — выключить флаг (без
деплоя). Валидация: REVENUE==Dodo по точкам, net profit сдвиг = Δвыручки.

## OAuth scopes (Dodo IS)

Токены минтит OAuth **sa** (marketplace-приложение `cnM4i`). Scope задаётся в
двух местах: настройки приложения на `marketplace.dodois.io/manage` **и**
`DODOIS_OAUTH_SCOPE` в `~/dodotool-sa/.env`. Текущий набор (имена — как в
marketplace, ВНИМАНИЕ к точным названиям):
- `accounting` — `/accounting/sales` (каналы дня)
- `sales` — `/finances/sales/units/monthly` (месячные агрегаты)
- `deliverystatistics` — `/delivery/statistics`, vouchers
- `productionefficiency` — `/production/productivity` (₽/шт·чел·ч). **НЕ `production`!**
- `stopsales` — `/production/stop-sales-*`, `/delivery/stop-sales-sectors`
- `incentives` — `/staff/incentives-by-members` (KC_LIVE)
- `user.role:read` — `/auth/roles/units` (имена юнитов)
- `offline_access` — refresh-токены (тихое продление в sa)

403 `InsufficientScopes` → добавить scope в приложение (marketplace) + в
`DODOIS_OAUTH_SCOPE` sa + пересоздать sa-контейнер + **re-consent** (юзер
заходит `https://sa.dodotool.ru/dodois/login` под нужным аккаунтом).

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

### Готовка зал — ВЕРНУЛИ (batched)
`/production/orders-handover-statistics?salesChannels=DineIn` возвращён через
batched call (≤30 юнитов/запрос). Пульс: Delivery+DineIn × today/LW = 4 запроса
всего, независимо от N (`board.py`, budget 20с). Финансы: DineIn в S16-синке →
`ops_metrics.avg_cook_restaurant_sec`. Остался только immutable-кэш `handover_lw`
(часть остатка #2 — ops-окна через success-флаг fetch).

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
- `lite_revenue_cache` (S19, миграция 0031) — Lite-выручка ЗАКРЫТЫХ месяцев с
  полной разбивкой каналов (delivery/restaurant/takeaway/other), JSONB,
  PK (planfact_key_id, project_id, month). Хелперы
  `store.get_lite_revenue_cache`/`upsert_lite_revenue_cache`, интеграция в
  `main._lite_revenue` (читаем кэш → fetch только missing → пишем; текущий/
  частичный месяц всегда live). Раньше Lite дёргал Dodo IS на КАЖДЫЙ просмотр
  истории. Условие кэшируемости — `_is_full_month` И не в live-окне
  (`store.is_period_in_live_window`, глубина с ключа).
- `ops_metrics` (S16/S18) для Финансов — персистентная месячная таблица (не
  TTL): для закрытых месяцев immutable, для текущего пере-синкается фоновым
  `_run_ops_sync`. Включает `avg_delivery_fulfillment_sec` (S18, миграция 0030,
  среднее время доставки — то же поле, что Пульс live).

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

### Готовка зал — СДЕЛАНО (вернули batched)
Фича возвращена в Пульс и Финансы через batched handover-statistics (4 запроса
всего вместо 2×N, см. подводный камень «Готовка зал — ВЕРНУЛИ»). Осталось только
immutable-кэш `handover_lw` — он часть остатка #2 (ops-окна через success-флаг).

### Stop-sectors timeout
`/delivery/stop-sales-sectors` стабильно timeout'ит в 8с. Endpoint
медленнее остальных. Решения: повысить budget или batch-вариант.

### Среднее время доставки — СДЕЛАНО (S18, миграция 0030)
Метрика «Среднее время доставки» (mm:ss, напр. 33:03) = `avgDeliveryOrderFulfillmentTime`
из `/delivery/statistics`. Пульс показывает live (`ops.delivery.avg_delivery_sec`).
Финансы: колонка `ops_metrics.avg_delivery_fulfillment_sec`, пишется в S16-синке
(`main`), мета `AVG_DELIVERY` (format mm_ss) в `store.OPS_METRICS` — плитка
рендерится из меты. Исторические значения/сравнение по месяцам — автоматически
(ops_metrics ключится по месяцу). Прошлые месяцы наполняются при (пере)синке.

## Аккаунты для тестов

Вход в приложение: `https://pnl.dodotool.ru/login` (сессионная авторизация pnl,
своя; не путать с OAuth Dodo IS, который теперь у sa для токенов).

- `andrey@dodotool.ru` (network admin, planfact_key=1, dodois `ask530` →
  через брокер sa sub `000d3a21…` «Коваль Андрей»). Пароль — у Андрея.
  Секреты в репо не храним; сменить через UI при необходимости.
- 2-й тенант: planfact_key=3, dodois `loderan2` → sub `5221ac3e…` «Agent Dodotool».

## Связанные docs

- `docs/dodois-api.md` — полный snapshot Dodo IS API (auto-gen scripts/pull_dodois_docs.py)
- `docs/planfact-agent-kit/` — внешняя дока PlanFact (для понимания target P&L)
- `docs/audits/*.md` — incident reports / audit notes

## Полезные команды (SA-VPS, Docker)

Все — через `ssh ask@94.26.246.138 '...'`. Контейнер `dodotool-pnl-api-1`,
Postgres `dodotool-sa-postgres-1`, БД `pnl_service`.

```bash
# Деплой (commit→push→build→up→migrate→healthcheck→автооткат)
cd ~/pnl-service && ./scripts/deploy.sh "feat: описание"

# Свежие логи / рестарт / статус
sudo docker logs -n 80 dodotool-pnl-api-1
sudo docker compose -f ~/pnl-service/docker-compose.pnl.yml restart
sudo docker ps --filter name=dodotool-pnl-api-1

# Health изнутри контейнера
sudo docker exec dodotool-pnl-api-1 python -c "import urllib.request as u;print(u.urlopen('http://localhost:8000/api/health',timeout=5).read())"

# Миграции
sudo docker exec dodotool-pnl-api-1 alembic current
sudo docker exec dodotool-pnl-api-1 alembic upgrade head

# DB shell
sudo docker exec -it dodotool-sa-postgres-1 psql -U dodotool_sa -d pnl_service

# Probe endpoint'а внутри контейнера (есть весь app + httpx)
sudo docker exec dodotool-pnl-api-1 python -c "<...>"

# Caddy (vhost pnl): правки в ~/dodotool-sa/Caddyfile, затем
sudo docker exec dodotool-sa-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```
