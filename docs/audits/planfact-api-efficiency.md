# Аудит эффективности использования PlanFact API

Дата: 2026-05-28. Проведён против `docs/planfact-agent-kit/` @ ac2b3b2f.

Цель: найти места, где наш `app/planfact.py` + `app/pnl.py` дёргают API
неоптимально или переизобретают то, что отдаётся готовым.

## Высокоприоритетные находки

### 1. Заменить `GET /operations` + ручной build_pnl на `POST /api/v2/reports/opu`

**Проблема.** `app/planfact.py:203-264` (`fetch_all_operations`) тянет 10k+
сырых операций за месяц (30-50 МБ JSON на XFood-ключе), затем
`pnl.py:568-644` руками группирует `operationParts` по
`(projectId, operationCategoryId)`, считает signed-суммы и pct_of_revenue.

**Что есть в kit.** `REPORTS_V2.md:81-153` описывает
`POST /api/v2/reports/opu` с `reportGenMethod: "Projects"` и
`isCalculation: true|false` — возвращает уже готовый ОПУ
«проект × строка статьи × сумма» одним вызовом. Прямо рекомендован вместо
ручной сборки:
- `OPERATIONS.md:318-332` — «грузить сырые `/operations` и считать агрегат
  вручную — только если отчётный endpoint не покрывает сценарий»
- `REPORTS_V2.md:286-288` — «не строить ОПУ вручную из `/operations`, если
  v2 endpoint покрывает запрос»
- `AGENTS.md:87` — «Не начинать операции с full `GET /api/v1/operations`,
  если подходит `POST /operations/list`»

**Ожидаемый эффект.** Снижение трафика на порядок (агрегат вместо тысяч
операций), уход от хрупкой логики `parallel-split по датам` (находка 2),
полное соответствие методам учёта (флаг `isOpuCalculation` у Accrual —
`OPERATIONS.md:409-415` предупреждает, что нужен ручной учёт; v2 сам
учитывает).

**Caveat.** При кастомном маппинге статей на P&L-строки шаблона придётся
обогащать ответ локально, либо оставить fallback на raw operations только
для нестандартного шаблона. Также `REPORTS_V2.md:343` — для
`OperationCategory` всегда нужны `isPeriodDetail: true` + `standardPeriod`,
для `Projects` это ограничение не действует.

### 2. Наш recursive-split `/operations` — kit подтверждает диагноз, но даёт обход

**Проблема.** Комментарий `planfact.py:215` («PF игнорирует offset/page»)
**подтверждается**:
- `QUICKSTART.md:137` — «не рассчитывай на `offset` и `limit` как способ
  уменьшить ответ `/operations`»
- `README.md:207` повторяет

Наш split — корректный workaround для `GET /operations`. Но ни kit, ни мы
не используем `POST /api/v1/operations/list`, у которого этот caveat **не
описан**, и который kit называет default-маршрутом для read-only.

**Что есть в kit.** `OPERATIONS.md:24-35` — default route для
operation-driven сценариев — `POST /api/v1/operations/list`. Server-side
фильтры: `searchString`, `contrAgentId[]`, `projectId[]`, `accountId[]`,
`accountCompanyId[]`, `operationDateStart/End`, `lastModificationDateStart/End`,
`importLogId` (`OPERATIONS.md:305`, `OPERATIONS.md:610-624`, `OPERATIONS.md:648-655`).

**Caveat** `OPERATIONS.md:227-229`: `operations/list` с фильтром по дате
может отсекать плановые операции с будущими датами — для accrual-сценария
за прошлые месяцы это не проблема, но при текущем месяце нужен post-check.

**Ожидаемый эффект.** Если перейти с raw `/operations` на v2 reports
(находка 1) — этот вопрос отпадает. Если raw нужен (drill-down
`/api/operations` в `main.py:1073, 1180`) — `POST /operations/list` даст
явные server-side фильтры, которых у нас сейчас нет.

### 3. `GET /api/v1/projects/calculations` для «план vs факт»

**Что есть в kit.** `LOOKUPS.md:269-278` — `GET /api/v1/projects/calculations`
возвращает за один запрос `incomePlan / outcomePlan` (план из бюджета) и
`incomeFact / outcomeFact` (факт в выбранном методе) на массив
`projectId[]`. «Единственный короткий endpoint для плана + факта по
проектам в одном ответе» (`LOOKUPS.md:278`). Связку `BUDGETS + bizinfos`
для этого кейса kit прямо запрещает (`README.md:227`).

**Ожидаемый эффект.** Если пользователи поддерживают бюджеты в PlanFact —
даём в дашборде «план vs факт по выручке/расходу проекта» в один запрос.
Это **не замена** `pnl_targets` (там % от выручки, кастомные строки), а
отдельный новый use-case.

**Caveat** `LOOKUPS.md:314-318` — НЕ суммировать строки `projects/calculations`
в company-wide итог.

## Средний приоритет

### 4. `_normalize_operation_parts` (`main.py:1097-1141`) — знак считаем по `op_type`

Делаем `sign = -1 if op_type == "Outcome" else 1`. Работает для типовых
случаев, но в `pnl.py:625-633` cross-check с `info.op_type` категории есть,
а в `_normalize_operation_parts` нет — на возвратах (`Outcome` в
`Income`-категории) знак может быть инвертирован.

**Ссылки kit:** `OPERATIONS.md:428-432`, `OPERATIONS.md:450-471`.

### 5. Пропущенные server-side фильтры в drill-down `/api/operations`

В `main.py:1073, 1180` передаём только дату, projectId, categoryIds.
Доступно ещё:
- `contrAgentId[]` (`OPERATIONS.md:648-655`) — фильтр по контрагенту
- `accountId[]`, `accountCompanyId[]` — фильтр по ЮЛ/счёту
- `searchString` через `POST /operations/list` (`OPERATIONS.md:474-479`)
- `lastModificationDateStart/End` (`OPERATIONS.md:497`) — инкрементальное
  обновление кэша

**Эффект.** Размер drill-down ответа уменьшится на порядок, не нужно
постфильтрация.

### 6. Кэш справочников: `filter.changesFromDate` вместо TTL

`README.md:248-250` рекомендует для refresh справочников использовать
`filter.changesFromDate`. У нас в `planfact.py:111-119` чистый TTL —
после истечения тянем полный список. С `changesFromDate` можно держать
persistent-кэш и подгружать только изменённые. Для P&L Dashboard эффект
скромный (categories/projects редко меняются), но для долгих сессий —
экономия.

**Webhooks отсутствуют.** Для самих операций инкремент только через
`lastModificationDateStart/End` в `POST /operations/list`.

### 7. Use-cases для DASHBOARDS / BALANCE / DEALS

Из kit нашли два реалистичных кейса:

- **`POST /api/v1/dashboards/accountbalance`** (`DASHBOARDS.md:153-167`) —
  остатки на расчётных счетах франчайзи на конец месяца. Карточка
  «сколько кэша в бизнесе» рядом с P&L.
- **`POST /api/v2/reports/dds`** (`REPORTS_V2.md:169-220`) — денежный
  поток по проектам по дате оплаты. Часто путают с accrual P&L; отдельная
  вкладка «cashflow» = меньше вопросов «почему прибыль есть, а денег нет».

**DEALS, DOCUMENTS** для Dodo Pizza (продажа пицц штучно) — оверкил.
**BALANCE** (бухгалтерский баланс) — оффтопик для франчайзи P&L.

## Не подтвердилось

- **Пагинация cursor/total/take/skip для `/operations`** — нет, наш
  recursive-split вынужденная мера. Правильный путь не «починить
  пагинацию», а «не использовать `GET /operations` для агрегатов»
  (находка 1).
- **Webhooks / If-Modified-Since** — нет. Только rate-limit headers
  (`X-RateLimit-Limit`, `X-Quota-*` в `QUICKSTART.md:21-27`) и
  `changesFromDate` / `lastModificationDate*` как pull-инкремент.
- **Двусторонняя синхронизация наших `pnl_targets` с BUDGETS PF** —
  слабо обоснована. Наши таргеты — % per-метрика, БДР PF — абсолютные
  суммы по статьям (`BUDGETS.md:42-59`). Разные структуры. Но добавить
  блок «выполнение бюджета» через `projects/calculations` (находка 3) —
  отдельный сценарий, не замена.

## Где kit не помог

- **Лимиты `POST /operations/list`**. Есть ли тот же caveat `items==10000
  при total==0`, что у `GET /operations` (`OPERATIONS.md:636`) — kit не
  пишет. Нужно проверить эмпирически перед заменой `fetch_all_operations`.
- **Поведение v2 reports на больших периодах / при `Projects` с 30+
  проектами**. Структуру ответа описывает, soft-rate-limit нет. Перед
  миграцией прогнать на XFood-ключе 12 мес × все проекты.
- **Mapping наших custom P&L-строк (UC, LC, DC, EBITDA по своей формуле)
  на сырые строки `reports/opu`**. Kit описывает только стандартные
  profit-метрики (`isGrossProfit`, `isEbitda`), наши шаблоны кастомные —
  миграция потребует промежуточного слоя.

## Файлы для следующих шагов

- `app/planfact.py` — добавить `list_operations_post` (POST /operations/list)
  и `report_opu` (POST /api/v2/reports/opu).
- `app/pnl.py` — `build_pnl` может принимать готовый агрегат из
  `reports/opu` вместо raw operations через новый параметр (по аналогии
  с `cached_aggregates`).
- `app/main.py:1073, 1180` — добавить `contrAgentId`, `searchString` в
  `/api/operations`.
