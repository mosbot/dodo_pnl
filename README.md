# P&L Dashboard · PlanFact

Веб-сервис, который в реальном времени тянет данные из PlanFact API и строит детальный P&L по пиццериям с расчётом процентов от выручки, сравнением периодов и контролем целей (Delivery Cost / Kitchen Cost и др.).

## Что умеет

- **P&L по проектам** в единой сводной таблице: Revenue → UC → LC → DC → Opex → EBITDA → Net Profit. Каждая статья показана и в рублях, и в % от выручки пиццерии.
- **Период**: произвольные даты, плюс пресеты (этот/прошлый месяц, квартал, YTD, последние 30 дней).
- **Сравнение с предыдущим периодом** — колонки Δ (руб. и п.п.).
- **Фильтр по проектам** — чекбоксы в боковой панели.
- **Drill-down**: клик по ячейке открывает список операций этой категории в этом проекте за выбранный период.
- **Цели по статьям** (UC / LC / DC / Rent / Marketing) — отдельно для каждой пиццерии. Отчёт «Отклонения от целей» подсвечивает превышение.
- **Ручное обновление** — кнопка «Обновить данные» сбрасывает кэш и перезапрашивает PlanFact.

## Архитектура

```
pnl-service/
├── app/
│   ├── main.py         FastAPI: роуты, auth, статика
│   ├── planfact.py     HTTP-клиент PlanFact + in-memory TTL-кэш
│   ├── pnl.py          классификация категорий, агрегация, сравнение
│   ├── storage.py      SQLite: цели и пользовательский маппинг
│   ├── schemas.py      Pydantic-модели
│   └── config.py       pydantic-settings из .env
├── static/             index.html + styles.css + app.js (Chart.js)
├── data/               SQLite-файл (монтируется как volume)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

PlanFact не отдаёт готовый P&L — мы берём `/operationcategories` (дерево статей) и `/bizinfos/incomeoutcomehistorybyoperationcategories` (суммы по категориям в разрезе проектов), классифицируем статьи эвристикой по ключевым словам в названии, при необходимости переопределяем маппинг через `POST /api/mappings`.

Цели и пользовательский маппинг хранятся в SQLite (`data/pnl.db`).

## Запуск на VPS (Docker)

```bash
# 1. Склонируйте проект на сервер
scp -r pnl-service/ user@your-vps:/opt/

# 2. Зайдите и настройте окружение
ssh user@your-vps
cd /opt/pnl-service
cp .env.example .env
nano .env   # впишите PLANFACT_API_KEY и при желании BASIC_AUTH_*

# 3. Запустите
docker compose up -d

# 4. Проверьте
curl http://localhost:8000/api/health
# {"status":"ok","planfact_key_set":true}
```

Логи:
```bash
docker compose logs -f pnl
```

Остановить / обновить:
```bash
docker compose down
docker compose up -d --build
```

## Где взять PlanFact API-ключ

Личный кабинет PlanFact → **Настройки → Безопасность → API-ключи** → «Создать ключ». Скопируйте значение и положите в `.env`:

```
PLANFACT_API_KEY=xxxxxxxxxxxxxxxxxxxx
```

## Проброс в интернет (nginx + HTTPS)

Пример конфига nginx перед контейнером:

```nginx
server {
    listen 443 ssl http2;
    server_name pnl.example.com;

    ssl_certificate     /etc/letsencrypt/live/pnl.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pnl.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Доступ по паролю — пропишите в `.env`:
```
BASIC_AUTH_USER=admin
BASIC_AUTH_PASSWORD=strong-password-here
```

## API (кратко)

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/health` | Пинг |
| GET | `/api/projects` | Список проектов из PlanFact |
| GET | `/api/categories` | Дерево статей + текущий маппинг |
| GET | `/api/pnl?date_start=…&date_end=…&project_ids=…&compare_start=…&compare_end=…` | Собранный P&L |
| GET | `/api/operations?date_start=…&date_end=…&project_id=…&category_id=…` | Drill-down |
| POST | `/api/refresh` | Сбросить кэш |
| GET \| POST \| DELETE | `/api/targets` | CRUD целей |
| POST | `/api/mappings` | Переопределить категорию → P&L-код |

## Настройка целей

В интерфейсе — кнопка **«Цели»** в шапке. Для каждой пиццерии задаёте максимальный % от выручки по метрикам:
- **UC** — Unit Cost (себестоимость продуктов)
- **LC** — Labor Cost (ФОТ)
- **DC** — Delivery Cost (курьеры, упаковка)
- **RENT** — аренда
- **MARKETING** — маркетинг

Типичные ориентиры для пиццерий: UC ≤ 30%, LC ≤ 22%, DC ≤ 12%, RENT ≤ 8%, MARKETING ≤ 5%.

В таблице и в виджете «Отклонения от целей» превышение подсвечивается красным.

## Если эвристика классификации ошиблась

Откройте `GET /api/categories` — увидите список всех статей PlanFact и текущий `pnl_code`. Чтобы переопределить:

```bash
curl -X POST http://localhost:8000/api/mappings \
  -H 'Content-Type: application/json' \
  -d '{"planfact_category_id":"12345","pnl_code":"DC"}'
```

Допустимые `pnl_code`:
`REVENUE, UC, LC, DC, RENT, MARKETING, FRANCHISE, OTHER_OPEX, OTHER_INCOME, MGMT, INTEREST, TAX, DIVIDENDS`

После изменения маппинга нажмите «Обновить данные» в интерфейсе.

## Локальный запуск (без Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # и заполните PLANFACT_API_KEY
uvicorn app.main:app --reload --port 8000
```

Откройте http://localhost:8000.

## Известные нюансы

- PlanFact не даёт webhooks — обновление только по кнопке (или по расписанию внешним cron'ом на `POST /api/refresh`).
- Формат ответа `/bizinfos/incomeoutcomehistorybyoperationcategories` зависит от версии API; `_normalize_history()` в `main.py` обрабатывает и плоский, и вложенный по проектам ответ.
- Первая загрузка после старта контейнера медленнее (наполнение кэша). TTL кэша — 5 минут (меняется через `CACHE_TTL` в `.env`).
