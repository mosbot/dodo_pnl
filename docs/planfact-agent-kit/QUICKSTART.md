# ПланФакт API — QUICKSTART для AI-агента

Этот файл — короткий technical index. Канонические policy-правила, router, budgets и кэш находятся в `README.md`. Правила поведения агента — в `AGENTS.md`, но они не выбирают endpoint. Базовая бизнес-логика (методы, даты, отчёты, факт/план) и бизнес-семантика — в `USE_CASES.md`. Доменные technical details вынесены в `OPERATIONS.md`, `REPORTS.md`, `BUDGETS.md`, `DEALS.md`, `LOOKUPS.md`, `DOCUMENTS.md`, `QUICKFILTERS.md`.
Business routing находится в `README.md → Intent Router`. Для типовых read-only сценариев не открывать `apidoc`, если `README.md` и файл по сущности уже содержат endpoint, минимальные параметры и known caveats. `apidoc.planfact.io` использовать как contract-reference для write, редких параметров, непокрытых схем или ошибок contract. Новая сессия не наследует кэш предыдущей: если в текущей сессии нужный слой данных ещё не загружен, считать кэш пустым.

Важно: правила оформления локальных HTML-отчетов и страниц, включая палитру `app.planfact.io` и русскоязычную выдачу по умолчанию, зафиксированы в `README.md` как часть слоя `delivery`.

## 1. Подключение

- Base URL: `https://api.planfact.io`
- Auth: заголовок `X-ApiKey`
- Обязательные заголовки: `Accept: application/json`, `Content-Type: application/json`
- Источник ключа по умолчанию: `.env` в рабочей папке, переменная `PLANFACT_API_KEY`

Если `.env` отсутствует или значение пустое, агент должен остановиться и попросить пользователя настроить `.env` по образцу `.env.example`. Не просить вставлять ключ в чат.

### Лимиты из HTTP-заголовков

API может возвращать заголовки лимитов в HTTP-ответе:

| Заголовок | Значение |
|---|---|
| `X-RateLimit-Limit` | максимально допустимое количество запросов в минуту |
| `X-Quota-Limit` | максимально допустимое количество запросов в месяц |
| `X-Quota-Used` | использованное количество запросов за месяц, без учета текущего запроса |
| `X-Quota-Remaining` | оставшееся количество запросов за месяц, без учета текущего запроса |
| `X-Quota-Reset` | Unix Timestamp даты обновления квоты, начало следующего месяца |

Минимальный пример (POST с payload-файлом — предпочтительный формат для всех JSON-body запросов):

```bash
# 1. Создать payload-файл
cat > payload.json << 'EOF'
{
  "filter": {
    "periodStartDate": "2026-03-01",
    "periodEndDate": "2026-03-31",
    "standardPeriod": "Month"
  }
}
EOF

# 2. Отправить запрос
curl -s -X POST "https://api.planfact.io/api/v1/dashboards/accountbalance" \
  --data-binary @payload.json \
  -H "X-ApiKey: <KEY>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json"
```

Детали по остаткам в базовых «Показателях» → `DASHBOARDS.md`. Для раздела «Мои счета» используется `GET /api/v1/bizinfos/accountshistory`. Для раздела «Показателей ПРО» используется `POST /api/v2/dashboardpro/accountbalance`. Для ДДС — `POST /api/v2/reports/dds` с аналогичным подходом → `REPORTS_V2.md`.

⚠️ Для `BizInfos`-history endpoint'ов (`/api/v1/bizinfos/incomeoutcomehistorybyoperationcategories`, `/incomeoutcomehistorybyprojects`, `/incomeoutcomehistorybycontragents`, `/accountshistory`) рабочий маршрут на стенде — `GET` с query-параметрами. Не отправлять их как `POST` с JSON body по аналогии с `reports/*`, `dashboards/*` или `dashboardpro/*`.

## 2. Формат ответа и ошибки

Все endpoint'ы возвращают общую обёртку:

```json
{
  "data": { "...": "..." },
  "isSuccess": true,
  "errorMessage": null,
  "errorCode": null,
  "errorCodeTitle": null,
  "errors": null
}
```

Для списков `data` обычно содержит:

```json
{
  "items": [],
  "total": 0,
  "deletedItems": [],
  "totalDeleted": 0
}
```

Правила:

- всегда сначала проверяй `isSuccess`;
- если `isSuccess == false`, читай `errorMessage` и `errors`;
- в ответе пользователю не показывай сырые `errorCode`, `errorCodeTitle` или enum-имя как основное объяснение; клиентские формулировки access-ошибок находятся в `README.md → Трактовка ограничений доступа`;
- не повторяй запрос вслепую только потому, что ответ неожиданной формы.

### Быстрая трактовка ответов

- `403`, `forbidden`, `нет доступа`, `module unavailable`, `tariff`, `subscription` — это ограничение доступа к текущему штатному сценарию.
- `200` + `isSuccess == true` + пустой `data` — это отсутствие данных по выбранным параметрам, а не access-ошибка.
- Ошибка валидации, schema error, неверный формат даты или enum — это ошибка payload, а не ограничение доступа.

Типовые причины ошибок:

- невалидный или просроченный API key;
- неверный формат даты;
- несуществующий ID;
- превышение лимитов.

## 3. HTTP 429 и write-запросы

В один момент может выполняться только один write-запрос (`POST`, `PUT`, `DELETE`) в бизнесе. Параллельные write могут вернуть HTTP `429`.

- write-запросы выполнять строго последовательно;
- при `429` подождать `2–5` секунд и повторить;
- не повторять больше `3` раз;
- если `429` сохраняется, сообщить пользователю.

На `GET` это ограничение не распространяется.

## 4. Общие технические правила

Для lookup, server-side фильтров и domain-specific caveats открывай соответствующий файл:

- `LOOKUPS.md` — справочники, `filter-first lookup`, финансовые агрегаты по контрагентам (`GET /contragents/calculated/{id}`, `{id}/additional`) и по проектам (`projects/calculations`, `projects/{id}/summary`)
- `OPERATIONS.md` — `/operations`, `/operations/list`, `operationDate`, `calculationDate`, `operationParts`
- `REPORTS.md` — `BizInfos` (v1): cash-history, Платёжный календарь
- `DEALS.md` — сделки
- `DOCUMENTS.md` — `invoice-documents`
- `BUDGETS.md` — бюджеты
- `QUICKFILTERS.md` — быстрые фильтры / сохраненные фильтры, публичные методы `QuickFilters`

### Пагинация

Обычные списковые endpoint'ы поддерживают:

| Параметр | По умолчанию | Макс  |
| -------- | ------------ | ----- |
| `offset` | 0            | —     |
| `limit`  | 10000        | 10000 |

Правила:

- если `total > offset + limit`, догружай страницы через `offset`;
- если `total > 20 000`, не загружай всё молча — сначала предупреди пользователя;
- исключение: не рассчитывай на `offset` и `limit` как на способ уменьшить ответ `/operations` на `api.planfact.io`.

### `value` vs `valueInUserCurrency`

- `value` — сумма в валюте счёта или части операции;
- `valueInUserCurrency` — сумма в валюте пользователя.

Для любых агрегаций, сводок и сравнений по нескольким валютам использовать `valueInUserCurrency`. `value` использовать только для показа суммы в исходной валюте.

### Boolean flags

Для boolean-полей `false` — это значимое значение, а не «пусто».

- для `isCalculationCommitted`, `isCommitted`, `closed` и похожих флагов не подставлять fallback через `// true`;
- в `jq` выражение вида `.isCalculationCommitted // true` опасно: если поле равно `false`, результатом станет `true`;
- для accrual-фильтрации использовать явные проверки: `.isCalculationCommitted != false`, `.isCalculationCommitted == true`, `.isCalculationCommitted == false`.

### Формат дат

**Правило для рабочего стенда: все даты в параметрах запросов передавать в формате `YYYY-MM-DD`.**

Это касается всех фильтров без исключения:
`operationDateStart`, `operationDateEnd`, `calculationPeriodDateStart`, `calculationPeriodDateEnd`, `filter.currentDate`, `periodStartDate`, `periodEndDate` и любых других date-параметров.

Примеры:

- `operationDateStart=2019-10-01`
- `filter.currentDate=2026-03-30`
- `filter.periodStartDate=2020-08-01`
- `filter.periodEndDate=2020-08-31`

⚠️ **Важно:** apidoc может показывать тип `date-time` и формат `YYYY-MM-DDTHH:MM:SS` для некоторых полей. На рабочем стенде это не работает — сервер возвращает ошибку `The field ... is invalid`. Всегда использовать `YYYY-MM-DD`. Формат `date-time` встречается только в **ответах** API, не в параметрах запросов.

Исключение, подтверждённое для budget write на `2026-04-14`: у `POST /api/v1/budgets` и `PUT /api/v1/budgets/{budgetId}` верхнеуровневые `startDate` / `endDate` действительно передаются как `date-time`, но одного swagger-типа недостаточно. Для этих полей нужно ориентироваться на live-template или уже успешный write-пример из этого аккаунта; в частности, `endDate` может валидироваться как **первый день последнего месяца бюджета**, хотя в `GET` тот же бюджет потом возвращается с последним днём месяца. Важно: `POST /api/v1/budgets` создаёт только оболочку бюджета; строки `versions[].info.items` пишутся последующим `PUT /api/v1/budgets/{budgetId}`. Детали и preflight-правила → `BUDGETS.md`.

### Параллельные read-запросы

Независимые read-запросы выполнять **параллельно**, а не последовательно, только если они уже выбраны `README.md → Intent Router` как необходимые distinct data sources.

Не добавлять endpoint только потому, что его можно выполнить параллельно. Слово `дашборд`, `HTML`, `график` или `страница` само по себе не расширяет набор API-вызовов.

### `POST` с JSON body через shell

Для `curl`-запросов с JSON body не полагайся на длинный inline `--data '{...}'`, если команда собирается в shell-строку агентом.

- по умолчанию предпочитай payload-файл и `--data-binary @payload.json`;
- это снижает риск сломанного JSON из-за quoting / escaping в shell;
- если API возвращает ошибку вида `Unexpected character encountered while parsing value...`, сначала подозревай не endpoint, а невалидный JSON-body;
- после такой ошибки не меняй маршрут запроса, пока не перепроверен сам body.

⚠️ Это правило относится только к endpoint'ам, которые действительно принимают JSON body. Для `BizInfos`-history endpoint'ов из `REPORTS.md` использовать `GET` + query string, а не payload-файл.

### Нормализация пользовательских периодов

Перед запросом всегда переводи естественный язык в точные границы периода.

Примеры:

- `за 2019 год` -> `2019-01-01` ... `2019-12-31`
- `с 2019 по 2021 год` -> `2019-01-01` ... `2021-12-31`
- `за март 2020` -> `2020-03-01` ... `2020-03-31`

Если строишь помесячный график, загрузи диапазон один раз и построй месячные buckets локально.

## 5. Маршрутизация

Business routing находится в `README.md → Intent Router`.

Этот файл не выбирает endpoint по бизнес-смыслу. Использовать его только для технических правил: подключение, ошибки, пагинация, формат дат, параллельность уже выбранных read-запросов и shell-safe JSON.

Если нужен точный маршрут:

1. открыть `README.md`;
2. выбрать строку в `Intent Router`;
3. открыть только указанный файл по сущности;
4. возвращаться в `QUICKSTART.md` только при технической проблеме запроса.
