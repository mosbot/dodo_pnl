# pnl-service — P&L Dashboard для франчайзи Dodo Pizza

Многотенантный SaaS-дашборд: тянет данные из **PlanFact** (P&L по проектам)
и **Dodo IS** (операционные метрики), строит детальный отчёт о прибылях и
убытках по пиццериям и оперативную сводку сети за день.

Прод: `https://pnl.dodotool.ru` (VPS `dodotool.ru`).

## Стек

FastAPI + SQLAlchemy 2.0 (async) + asyncpg + PostgreSQL. Фронт — ванильный
JS + Chart.js (self-host), без сборки. Деплой — systemd + venv + nginx.

## Что умеет

- **P&L по проектам** (`/`) — Revenue → UC/LC/DC → Opex → EBITDA → Net
  Profit, в рублях и % от выручки, сравнение периодов (LFL / MoM), цели по
  статьям, drill-down по операциям, экспорт в xlsx. Источник агрегата —
  `POST /api/v2/reports/opu` PlanFact (см. `docs/audits/v2-reports-migration-plan.md`).
- **Сетевой scoreboard дня** (`/board`) — выручка всех точек сети с дельтой
  vs прошлый аналогичный день недели, активные стопы, ops-метрики (Кухня +
  Доставка) из Dodo IS, месяц LFL, прогноз. Для territorial/network-админа.
- **Настройки** (`/settings`) — профиль, сессии, интеграции, шаблон P&L,
  проекты, метрики, цели, пользователи (для админов).

## Доступ и роли

Серверные сессии (argon2id-пароли, HttpOnly+Secure+SameSite cookie,
rate-limit логина, audit log). Роли: `super_admin` / `network_admin` /
`user`. Видимость строк P&L и проектов гейтится по `visibility_level`
(10 управляющий → 100 партнёр) и per-user hidden-list.

## Мультитенантность

Все данные скоупятся по `planfact_key_id` (сеть = PF-ключ) или `owner_id`.
PlanFact-ключи и привязка к Dodo IS — в БД (`planfact_keys`, `users`),
не в env. Схема БД — `pnl_service.*`; соседняя `public.dodois_credentials`
(read-only) хранит access-token Dodo IS, refresh делает соседний сервис.

## Структура

```
app/
  main.py          FastAPI app + endpoints
  board.py         /api/board — сетевой scoreboard дня
  day_window.py    временные окна MSK для /board
  dodois_client.py клиент Dodo IS API
  planfact.py      клиент PlanFact (вкл. report_opu v2)
  pnl.py           сборка P&L, классификация статей
  pnl_v2.py        адаптер v2 reports/opu → агрегаты
  store.py         DB-операции
  models.py        SQLAlchemy 2.0 модели
  auth/            пароли, сессии, роли, audit
  config.py        pydantic-settings из .env
alembic/versions/  миграции 0001→0026
static/            board.* / index / settings + app.js + vendor/chart
scripts/
  deploy.sh        атомарный деплой (git + migrate + restart + откат)
docs/
  dodois-api.md    снапшот Dodo IS API
  planfact-agent-kit/  дока PlanFact
  audits/          incident-репорты и аудиты
CLAUDE.md          подробный контекст репозитория
```

## Локальный запуск

Нужен Python 3.10+ и доступный PostgreSQL. Заполнить `.env`
(см. `.env.example`), затем:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Либо `./start.sh` (сам создаёт venv и ставит зависимости).

## Деплой (прод)

Через git, одной командой (подробности — в CLAUDE.md):

```bash
# правки скопировать на VPS, затем:
ssh claude@dodotool.ru 'cd /home/claude/pnl-service && ./scripts/deploy.sh "feat: описание"'
```

`scripts/deploy.sh` атомарно: `git commit` → `git push` → `pip install`
(если менялся requirements) → `alembic upgrade head` → рестарт →
health-check, **с автооткатом кода при провале**.

## Документация

- `CLAUDE.md` — детальный контекст: окна /board, источники Dodo IS,
  известные подводные камни, открытые задачи.
- `docs/audits/` — аудиты и план миграции на PlanFact v2.
