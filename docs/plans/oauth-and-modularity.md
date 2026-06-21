# План: свой OAuth Dodo IS + модульность (PlanFact опционален)

Черновик плана реализации с промежуточными тестами. Принцип на всех этапах:
**ничего не ломаем в проде** — каждый шаг за флагом/гибридом, проверяется
non-regression против текущих чисел, деплой через `scripts/deploy.sh`
(health-check + автооткат).

## Последовательность (согласовано)

1. **Workstream 1 — свой OAuth** (отвязка от токенов кассы). Самодостаточно,
   за гибридным резолвером — нулевое изменение поведения для текущих юзеров.
2. **Workstream 2 — модульность**, начиная с Фазы 0 «PlanFact опционален»
   (именно она вместе с OAuth открывает рынок клиентов без кассы и без PF).

Переезд на свой сервер/БД — отложен (отдельный ops-этап позже).

---

## Workstream 1 — Свой OAuth-клиент Dodo IS

**Цель.** Получать и рефрешить СВОИ access/refresh токены (Authorization
Code flow), убрать зависимость от `public.dodois_credentials` (таблицы кассы).
Любой франчайзи Dodo сможет подключиться, не будучи пользователем кассы.

### Предусловия (вне кода — действия владельца)
- Зарегистрировать приложение в кабинете разработчика
  (`marketplace.dodois.io/manage/apps`): получить `client_id`, `client_secret`,
  задать `redirect_uri` (напр. `https://pnl.dodotool.ru/api/integrations/dodois/callback`),
  выбрать scopes (accounting, production, delivery, finance, incentives,
  stopsales, franchisee:read).
- Тестовый Dodo-аккаунт для live-проверки (T4).

### Компоненты
1. **Миграция 0028** — хранение токенов на аккаунте (= `planfact_keys`):
   таблица `dodois_oauth(planfact_key_id PK, access_token_enc, refresh_token_enc,
   expires_at, scopes, connected_by_user_id, updated_at)`. Токены шифруются
   через существующий `crypto.encrypt_secret` (Fernet).
2. **`app/auth/dodois_oauth.py`** — клиент flow:
   - `build_authorize_url(state, redirect_uri, scopes)`,
   - `exchange_code(code, redirect_uri)` → `/connect/token`,
   - `refresh(refresh_token)` → `/connect/token` (grant_type=refresh_token).
   - Конфиг `client_id/secret/redirect_uri/scopes` — в `config.py`/`.env`.
3. **Endpoints** (admin-gated):
   - `GET /api/integrations/dodois/connect` → редирект на authorize + `state`
     (CSRF) в сессии;
   - `GET /api/integrations/dodois/callback?code&state` → проверка state →
     обмен кода → сохранение токенов → редирект в /settings;
   - `POST /api/integrations/dodois/disconnect` → очистка токенов.
4. **Гибридный токен-резолвер** — переписать `auth/tokens.get_dodois_token`:
   - есть OAuth-токены аккаунта → если протух (expires_at − буфер) → refresh +
     persist → вернуть access;
   - иначе → legacy fallback (`public.dodois_credentials` по
     `dodois_credentials_name`).
5. **Реактивный refresh** — в `with_dodois_retry` на 401: для OAuth-аккаунтов
   force-refresh → повтор (заменяет нынешнее «перечитать чужую таблицу»).

### Промежуточные тесты (gate'ы уверенности)
- **T1 (unit, без сети):** `build_authorize_url` содержит корректные
  `response_type=code`, `client_id`, `scope`, `redirect_uri`, `state`.
- **T2 (unit):** round-trip хранилища — encrypt→save→read→decrypt; логика
  «протух/не протух»; резолвер отдаёт OAuth-токен если есть, иначе legacy
  (оба замоканы). Без внешних вызовов.
- **T3 (unit):** при протухшем access вызывается `refresh`, новый токен
  персистится (мок `/connect/token`).
- **T4 (LIVE, ключевой):** реальный flow на ТЕСТОВОМ Dodo-аккаунте через
  `auth.dodois.io` нашим зарегистрированным приложением → получить настоящий
  токен → вызвать `/auth/roles/units` или `/franchisee/units` → **работает
  без таблицы кассы**. Это доказательство анлока.
- **T5 (non-regression):** существующие аккаунты (без OAuth-токенов)
  резолвятся через legacy; `build_board_payload` и `/api/pnl` для PiX дают
  идентичные текущим числа.
- **T6:** путь 401 → refresh → повтор (протухший access + валидный refresh).

### Раскатка
Флаг/гибрид: пока у аккаунта нет своих токенов — всё как сейчас. Подключение
OAuth — добровольное, через /settings. Когда все аккаунты переведены
(re-consent) — убрать чтение legacy-таблицы (отдельным PR).

---

## Workstream 2 — Модульность

### Фаза 0 — PlanFact опционален (открывает рынок вместе с OAuth)
**Цель.** Аккаунт может существовать без PlanFact; board работает полностью
без PF.
- Миграция: `planfact_keys.api_key` → nullable; добавить
  `source_type` ('planfact' | 'dodo' | 'sheet', default 'planfact').
- `get_planfact_key` и P&L-путь: при отсутствии PF не падать, а отдавать
  «нет источника P&L» (board и Dodo-метрики работают).
- **Тесты:** (a) аккаунт без api_key — board строится, числа корректны;
  (b) `/api/pnl` для PF-аккаунта не изменился (non-regression); (c) аккаунт
  без PF: `/api/pnl` отдаёт пусто/заглушку, а не 500.

### Фаза 1 — Абстракция источника P&L (`PnLSource`)
**Цель.** Вынести производителей агрегатов за интерфейс; поведение не меняется.
- Интерфейс `PnLSource.aggregates() -> {totals, cat_totals, revenue_by_channel,
  active_project_ids}` + шаблон. Реализации: `PlanFactV2Source`,
  `PlanFactRawSource` (обернуть существующее `v2_to_aggregates` / raw).
- `build_pnl` не трогаем (он уже это ест).
- **Тесты:** бит-в-бит совпадение результата до/после рефакторинга на PiX и
  Xfood (как при миграции S20). Это чистый рефактор — главный критерий
  «ничего не изменилось».

### Фаза 2 — Источник «таблица» (Google Sheets / Excel), без дрилл-ина
**Цель.** Импорт готового P&L клиента из таблицы; анализ без детализации.
- `SheetSource`: парсинг .xlsx (переиспользовать `planfact_export.py`) →
  `totals[(unit, code)]`; `cat_totals` пустой → дрилл отключён.
- **Классификация (одноразовая, не маппинг-пересчёт):** статья → уровень P&L
  (pnl_code) + какие статьи в какие метрики; автоподсказки по названию.
  Готовые уровни из таблицы (EBITDA/Маржа) берём как есть. Новые статьи →
  «не классифицировано» (инкрементально), переиспользуя механизм
  `unclassified`.
- Хранение как immutable месячные снэпшоты (паттерн `cache_history`).
- Гейт дрилла на фронте по `source_type`.
- **Тесты:** (a) импорт тестовой .xlsx → корректные уровни и сетевой rollup;
  (b) строка уровня из таблицы потребляется как есть (не пересчитывается);
  (c) новая статья ловится в unclassified; (d) дрилл скрыт для sheet-аккаунта;
  (e) LFL/MTD-выравнивание работает на импортированных периодах.

### Фаза 3 — Capabilities / подписки (per-unit)
**Цель.** Гейтинг модулей/фич по подписке (см. memory: marketplace,
`/marketplace/subscriptions`, capabilities). Делается, когда выходим на
маркетплейс. Детализация — отдельным планом.

---

## Cross-cutting (на всех фазах)
- Каждый шаг — за флагом/гибридом; прод-поведение неизменно до явного включения.
- Non-regression: сверять числа board / `/api/pnl` против текущих (probe-скрипты
  на проде, как делали в этой сессии).
- Деплой только через `scripts/deploy.sh` (health-check + автооткат); миграции
  аддитивные.
- Безопасность: токены под Fernet; OAuth `state` (CSRF); admin-gated endpoints;
  учитывать находки security-audit 2026-06-13.

## Открытые решения (для владельца)
- Регистрация приложения + `client_secret` в `.env` (предусловие T4).
- Redirect URI / домен для callback.
- Один OAuth-токен на аккаунт (подключивший) — ок для v1, или мультиконнект.
- Формат маппинг-экрана таблиц (Фаза 2) — отдельно проработать UX.
