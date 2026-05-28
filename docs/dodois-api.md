# Dodo IS API — справочник эндпоинтов

Источник: `docs.dodois.io`, project `cHJqOjExMTA4MQ` (Stoplight). Базовые URL по странам — `https://api.dodois.io/dodopizza/<country>` (RU: `/dodopizza/ru`, BY: `/dodopizza/by`, прочие на `api.dodois.com`).

Авторизация: OAuth Bearer (`Authorization: Bearer <access_token>`). Список юнитов пользователя — `GET https://api.dodois.io/auth/roles/units`.

Общие ограничения query-параметров (повторяются почти везде):
- `units` — до 30 UUID через запятую без пробелов;
- `from`/`to` — ISO 8601, обычно округление до часа, диапазон ≤ 31 день;
- пагинация (где есть) — через `skip`+`take` или `nextPageToken`.

## Что уже подключено в pnl-service

- `GET /production/productivity` — `salesPerLaborHour`, `productsPerLaborHour`, `avgHeatedShelfTime`, `ordersPerCourierLabourHour` → плитки производительности.
- `GET /delivery/statistics` — `unitsStatistics[]` (метрики доставки).
- `GET /delivery/vouchers` — список сертификатов за опоздание (с пагинацией) → плитка «Опоздания» (считаем штуки).
- `GET https://api.dodois.io/auth/roles/units` — список юнитов пользователя.

См. `app/dodois_client.py` — все три ручки идут параллельно по юнитам с retry и `_MAX_PARALLEL=12`.

## Что приоритетно добавить (Tier 1)

**Прямая связь с P&L строками:**

- **`GET /production/stop-sales-channels`** (`stopSalesBySalesChannels[]`) — стопы по каналам (доставка / самовывоз / ресторан). Поля: `startedAtLocal`/`endedAtLocal`, `reason`, `channelStopType`. Метрика: суммарные минуты простоя канала / месяц. Объясняет дыры в выручке.
- **`GET /production/stop-sales-ingredients`** — стопы по ингредиентам. Поля: `ingredientName`, `startedAtLocal`/`endedAtLocal`, `reason`.
- **`GET /production/stop-sales-products`** — стопы по продуктам. Аналогично.
- **`GET /accounting/write-offs/products`** (`writeOffs[].cost`, `writeOffPlaceName`, `reason`) и **`GET /accounting/write-offs/stock-items`** — формально это и есть наша строка «Потери» в P&L. Можно сверять с PlanFact и подсвечивать расхождения. Группировка по `reason` даёт почему списали.
- **`GET /accounting/staff-meals`** — питание персонала. Часто прокладывается отдельной строкой в L/C или Direct Cost.
- **`GET /accounting/cancelled-sales`** — отмены заказов с причинами. Прямой QA-сигнал и потерянная выручка.

**Labor / расписание (для L/C control):**

- **`GET /staff/shifts`** (`clockInFrom`/`clockInTo`, `staffTypeName`) — все закрытые смены за период с разрезом по `staffPositionName`, `staffTypeName`, `dayShiftMinutes`/`nightShiftMinutes`/`holidayMinutes`, `employmentTypeName`. Разбивка FOT по ролям: ₽/час по позициям (FOT_role / hours_role).
- **`GET /staff/positions`** — список должностей (маппинг в нашу терминологию).
- **`GET /staff/schedules`** + **`GET /staff/schedules/forecast`** — план vs факт; schedule adherence как ops-метрика.
- **`GET /staff/incentives-by-members`** + **`GET /staff/incentives/premium`** — премии и доплаты по сотрудникам. Помогает разобрать строку «Премии» в FOT.

**Sales decomposition / fact-cross-check:**

- **`GET /accounting/sales`** — сделки за период. Канал, тип оплаты, средний чек. Сверка с выручкой из PlanFact + декомпозиция по `salesChannel`.
- **`GET /finance/sales/units/daily`** (id `442ee262c9bab`) и месячный аналог — готовые агрегаты.
- **`GET /orders/new-clients-statistics`** — статистика по новым клиентам → объяснение LFL.

**Bonus: нативные таргеты Dodo IS**

- **`GET /units/monthly-goals`** (id `fdd52b85049d6`) и **`PATCH /units/monthly-goals`** (id `030305f218aa4`) — Dodo IS уже хранит «Цели на месяц» по заведению. Можно подтягивать как defaults в наши per-key targets, либо пушить наши значения обратно.

## Tier 2 (полезно, но не критично)

- `GET /production/orders-handover-time` — детальное время сборки/тепловой полки/готовки **по каждому заказу**. Тяжёлый. На плитку лучше `/production/orders-handover-statistics`.
- `GET /production/tracking-metrics/summary` — среднее время продукта на станциях.
- `GET /production/unit-workload-by-orders` / `unit-workload-by-products` — нагрузка по получасам. Для heatmap.
- `GET /delivery/couriers-orders` — заказы курьеров со временем поездки.
- `GET /delivery/delivery-sectors` + `GET /delivery/stop-sales-sectors` — стопы секторов доставки.
- `GET /delivery/efficiency` (in_development) — готовый агрегат `tripsWithOneOrderCount` / `forecastHitTripsPercentage`. Заменит самописный расчёт.
- `GET /accounting/defective-products` — забракованные продукты (рядом с write-offs).
- `GET /accounting/stock-consumptions-by-period` — расход сырья. Для food cost variance vs theoretical.
- `GET /accounting/incoming-stock-items` — приходы от поставщиков.

## Reference / справочники (грузить редко, кешировать)

`GET /accounting/products`, `/accounting/stock-items`, `/staff/positions`, `/units/...`, `/organisations/legal-entities`, `/organisations/legal-entity-types`, `/organisations/settlements`.

## Архитектурное замечание

Когда наберётся 6–8 ops-ручек, текущий паттерн `_fetch_X_one + _MAX_PARALLEL=12` начнёт дублировать код. Стоит вынести `fetch_per_unit(url, response_key)` — generic-функцию.

---

# Полный референс (auto-generated)

Ниже — все разобранные ручки: метод, путь, query, поля ответа. Сгенерировано `_scripts/pull_dodois_docs.py` (Stoplight nodes API).

## Доставка

### Доставка → Статистика
**`GET /delivery/statistics`**
> Возвращает основные метрики по доставке за выбранный период по пиццериям (`units`). ### Расчет некоторых показателей: 1. Процент доставки через курьерское приложение = `ordersWithCourierAppCount` / `deliveryOrdersCount` 2. Количество заказов на курьера в час = `deliveryOrdersCount` / (`couriersShiftsDuration` / 3600) 3. Загрузка кукрьеров = `tripsDuration` / `couriersShiftsDuration` ### Требования

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601

Response (application/json):
- `unitsStatistics`: array — Основные метрики доставки
- `unitsStatistics[].unitId`: string — Идентификатор пиццерии
- `unitsStatistics[].unitName`: string — Название пиццерии в форматах Сыктывкар-1 или Москва 0-1
- `unitsStatistics[].deliverySales`: number — Выручка с доставки
- `unitsStatistics[].deliveryOrdersCount`: integer — Количество заказов на доставку
- `unitsStatistics[].avgDeliveryOrderFulfillmentTime`: integer — Среднее время доставки (от оформления заказа до вручения клиенту) в секундах
- `unitsStatistics[].avgCookingTime`: integer — Среднее время приготовления в секундах
- `unitsStatistics[].avgHeatedShelfTime`: integer — Среднее время ожидания на тепловой полке в секундах
- `unitsStatistics[].avgOrderTripTime`: integer — Среднее время курьера с заказом в пути в секундах
- `unitsStatistics[].lateOrdersCount`: integer — Количество опоздавших заказов
- `unitsStatistics[].tripsCount`: integer — Количество поездок курьеров
- `unitsStatistics[].tripsDuration`: integer — Сумма времени всех поездок курьеров в секундах
- `unitsStatistics[].couriersShiftsDuration`: integer — Сумма продолжительности смены всех курьеров в секундах
- `unitsStatistics[].ordersWithCourierAppCount`: integer — Количество доставок с курьерским приложением

### Доставка → Сертификаты за опоздание
**`GET /delivery/vouchers`**
> Сертификаты за опоздание. > #### Доступно для следующих ролей: > > `Division administrator` - Администратор подразделения > > `Store Manager` - Менеджер офиса

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `vouchers`: array — Сертификаты за опоздание
- `vouchers[].orderId`: string — Id заказа
- `vouchers[].orderNumber`: string — Номер заказа
- `vouchers[].orderAcceptedAtLocal`: string — Дата и время приёма заказа (локальное время)
- `vouchers[].unitId`: string — Id юнита (заведения)
- `vouchers[].predictedDeliveryTimeLocal`: string — Предполагаемое время доставки (локальное время)
- `vouchers[].orderFulfilmentFlagAtLocal`: string — Время отметки курьером в приложении успешной доставки (время локальное)
- `vouchers[].deliveryDeadlineLocal`: string — Крайний срок доставки (локальное время)
- `vouchers[].issuerName`: string — Кем выдан сертификат
- `vouchers[].courierStaffId`: string — Id сотрудника-курьера
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Доставка → Заказы курьеров
**`GET /delivery/couriers-orders`**
> Метрики по каждому заказу курьеров. Время в параметрах в возвращаемом ответе отдаётся по UTCefficiency Параметры `from` и `to` определяют фильтрация заказов по дате их создания и внесенных изменений. ### Требования к query параметрам: - `units` не может быть больше 30 - `take` не может быть меньше 0 или больше 1000 > #### Доступно для следующих ролей: > > `Division administrator` - Администратор п

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `couriersOrders`: array — Заказы курьеров
- `couriersOrders[].orderId`: string — идентификатор заказа
- `couriersOrders[].orderNumber`: string — Номер конкретного заказа. Первая цифра показыает порядковый номер заказа, вторая цифра показывает количество выпекаемых позиций в заказе.
- `couriersOrders[].courierStaffId`: string — Идентификатор сотрудника-курьера
- `couriersOrders[].tripId`: string — уникальный идентификатор поездки (за одну поездку может доставляться несколько заказов)
- `couriersOrders[].unitId`: string — Идентификатор заведения
- `couriersOrders[].unitName`: string — Название заведения
- `couriersOrders[].staffShiftId`: string — Идентификатор смены сотрудника
- `couriersOrders[].handedOverToDeliveryAt`: string — Время, когда заказ отмечен отправленным в доставку. Касса доставки: отправление происходит при отправке с кассы доставки. Курьерское приложение: при нажатии кно
- `couriersOrders[].predictedDeliveryTime`: integer — Прогнозное время Яндекса или Google, за которое должен быть доставлен заказ + 4 минуты на действия на адресе заказа: доехать, припарковаться, подняться к клиент
- `couriersOrders[].deliveryTime`: integer — Фактическое время, за которое был доставлен заказ В секундах с округлением.
- `couriersOrders[].orderFulfilmentFlagAt`: string — Время отметки курьером в приложении успешной доставки. Возвращает null для заказов через КД
- `couriersOrders[].isFalseDelivery`: boolean — Была ли доставка заказа некорректной. Причины некорректных заказов: Неверная отметка геолокации: если курьер отметился у клиента в радиусе более, чем 300 метров
- `couriersOrders[].deliveryTransportName`: string — Вид транспорта курьера. Устанавливается во время отправки заказа через приложение или кассу доставки: авто, пеший, велосипед.
- `couriersOrders[].tripOrdersCount`: integer — Количество заказов, которые взял курьер в одну поездку
- `couriersOrders[].heatedShelfTime`: integer — Время ожидания заказа на тепловой полке в секундах с округлением
- `couriersOrders[].orderAssemblyAvgTime`: integer — Время сборки заказа, в котором участвует курьер. В секундах с округлением. Возможны два случая: 1. Курьер встал в очередь, и заказ появился после этого. Тогда д
- `couriersOrders[].isProblematicDelivery`: boolean — Были проблемы с доставкой
- `couriersOrders[].problematicDeliveryReason`: string — Причина проблемной доставки
- `couriersOrders[].wasLateDeliveryVoucherGiven`: boolean — Был выдан сертификат за опоздание
- `couriersOrders[].sectorId`: string — идентификатор сектора
- `couriersOrders[].sectorName`: string — Название сектора
- `couriersOrders[].numberOfCouriersInQueue`: integer — Количество курьеров в очереди в момент отправки заказа
- `couriersOrders[].orderFulfilmentFlagAtLocal`: string — Вовзращает UTC вместо локального времени
- `couriersOrders[].handedOverToDeliveryAtLocal`: string — Возвращает UTC вместо локального времени
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Доставка → Сектора доставки
**`GET /delivery/delivery-sectors`**
> Возвращает данные о секторах доставки ### Требования к query параметрам: 1. В `units` можно перечислить до 30 отделов в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; > #### Доступно для следующих ролей: > > `Division administrator` - Администратор подразделения > > `Store Manager` - Менеджер офиса

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `showDeleted` (boolean) — Флаг, указывающий необходимо ли показывать удаленные сектора
- `showSubSectors` (boolean) — Флаг, указывающий необходимо ли показывать под-сектора (нарисованные из менеджера смены)

Response (application/json):
- `deliverySectors`: array — Данные по секторам доставки
- `deliverySectors[].unitId`: string — Идентификатор пиццерии
- `deliverySectors[].unitName`: string — Название пиццерии в форматах Сыктывкар-1 или Москва 0-1
- `deliverySectors[].sectorId`: string — Id сектора доставки
- `deliverySectors[].sectorName`: string — Название сектора
- `deliverySectors[].isDeleted`: boolean — Сектор удален
- `deliverySectors[].isStopped`: boolean — Сектор находится в стопе
- `deliverySectors[].isSubSector`: boolean — Является ли сектор под-сектором, созданным из менеджера смены
- `deliverySectors[].geometry`: object — Объект в формате Geo-json, описывающий форму зоны доставки на карте

### Доставка → Стоп-продажи по секторам
**`GET /delivery/stop-sales-sectors`**
> Возвращает данные о стопах продаж за период для набора пиццерий (`units`). Стопы сгруппированы по пиццериям и секторам. ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 3. Диапазон дат между `to` и `from` параметрами не должен превышать 31 день; 4. Начальная дата периода `to

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601. Время указывается в UTC.
- `to` (string, required) — Конец периода в формате ISO 8601. Время указывается в UTC.

Response (application/json):
- `stopSalesBySectors`: array — Данные о стопах продаж сгруппированные по заведениям и секторам
- `stopSalesBySectors[].id`: string — Id стопа
- `stopSalesBySectors[].unitId`: string — Идентификатор пиццерии
- `stopSalesBySectors[].unitName`: string — Название пиццерии в форматах Сыктывкар-1 или Москва 0-1
- `stopSalesBySectors[].sectorName`: string — Сектор поставленный в стоп продажи
- `stopSalesBySectors[].isSubSector`: boolean — Является ли сектор под-сектором, созданным из менеджера смены
- `stopSalesBySectors[].startedAt`: string — Время начала стопа в UTC в формате ISO 8601
- `stopSalesBySectors[].startedAtLocal`: string — Время начала стопа в часовой зоне пиццерии в формате ISO 8601
- `stopSalesBySectors[].endedAt`: string — Время возобновления продаж в UTC в формате ISO 8601 (null - если еще не возобновлено)
- `stopSalesBySectors[].endedAtLocal`: string — Время возобновления продаж в часовой зоне пиццерии в формате ISO 8601 (null - если еще не возобновлено)
- `stopSalesBySectors[].suspendedByUserId`: string — Id пользователя, который инициировал стоп продаж (null - если данные о пользователе отсутствуют)
- `stopSalesBySectors[].resumedUserId`: string — Id пользователя, который возобновил продажи (null - если еще не возобновлено)

### Доставка → Эффективность (in_development)
**`GET /delivery/efficiency`**
> Возвращает метрики эффективности доставки за выбранный диапазон и по пиццериям (`units`). ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 3. Диапазон дат между `to` и `from` параметрами не должен превышать 31 день.

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601

Response (application/json):
- `unitDeliveryEfficiency`: array — Еффективность доставки по пиццериям
- `unitDeliveryEfficiency[].unitId`: string — Идентификатор пиццерии
- `unitDeliveryEfficiency[].unitName`: string — Название пиццерии в форматах Сыктывкар-1 или Москва 0-1
- `unitDeliveryEfficiency[].deliverySales`: number — Выручка с доставки
- `unitDeliveryEfficiency[].deliveryOrdersCount`: integer — Количество заказов на доставку
- `unitDeliveryEfficiency[].ordersWithCourierAppCount`: integer — Количество доставок с курьерским приложением
- `unitDeliveryEfficiency[].stopSalesCount`: integer — Количество стопов
- `unitDeliveryEfficiency[].stopSalesDuration`: integer — Продолжительность стопов (в секундах)
- `unitDeliveryEfficiency[].lateOrdersCount`: integer — Количество опоздавших заказов
- `unitDeliveryEfficiency[].forecastHitTripsPercentage`: number — Поездки попавшие в прогноз (в %)
- `unitDeliveryEfficiency[].incorrectTripsPercentage`: number — Некорректные поездки (в %) https://dodopizza.info/articles/692ec125-18c6-489e-ba30-955177601d4c
- `unitDeliveryEfficiency[].tripsWithOneOrderCount`: integer — Количество поездок с одним заказом
- `unitDeliveryEfficiency[].tripsWithTwoOrdersCount`: integer — Количество поездок с двумя заказами
- `unitDeliveryEfficiency[].tripsWithThreeOrdersCount`: integer — Количество поездок с тремя заказами
- `unitDeliveryEfficiency[].tripsWithFourOrdersCount`: integer — Количество поездок с четырьмя заказами
- `unitDeliveryEfficiency[].tripsWithFiveOrMoreOrdersCount`: integer — Количество поездок с 5 или более заказами

## Производство

### Производство → Время выдачи заказа
**`GET /production/orders-handover-time`**
> Возвращает время выдачи заказов за выбранный период по пиццериям (`units`). ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 3. Диапазон дат между `to` и `from` параметрами не должен превышать 31 день. ### Округление до целых секунд: Длительность событий (например, `cookingT

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601

Response (application/json):
- `ordersHandoverTime`: array — Время выдачи заказа
- `ordersHandoverTime[].unitId`: string — Идентификатор пиццерии
- `ordersHandoverTime[].unitName`: string — Название пиццерии в форматах Сыктывкар-1 или Москва 0-1
- `ordersHandoverTime[].orderId`: string — Идентификатор заказа
- `ordersHandoverTime[].orderNumber`: string — Номер заказа
- `ordersHandoverTime[].salesChannel`: string — Канал продажи
- `ordersHandoverTime[].orderTrackingStartAtLocal`: string — Время заказа в формате ISO 8601 (локальное время)
- `ordersHandoverTime[].trackingPendingTime`: integer — Время ожидания на трекинге в секундах с округлением
- `ordersHandoverTime[].cookingTime`: integer — Время приготовления заказа в секундах с округлением
- `ordersHandoverTime[].heatedShelfTime`: integer — Время ожидания заказа на тепловой полке в секундах с округлением
- `ordersHandoverTime[].assemblyTime`: integer — Время ожидания сборки заказа из приложения в ресторане в секундах с округлением. Заполняется только для заказов типа `Dine-in`, в других случаях - null
- `ordersHandoverTime[].orderSource`: string — Источник заказа
- `ordersHandoverTime[].orderTrackingStartAt`: string — Возвращает локальное время вместо UTC

### Производство → Метрики с трекинга (Сводные)
**`GET /production/tracking-metrics/summary`**
> Позволяет получить сводную информацию о времени, проведенном продуктом на различных станциях производства за указанный период. При запросе за определенный период (`from`, `to`) и запросе по нескольким заведениям (`units`), будет возвращено среднее значение по каждому продукту за весь интервал from to отдельно для каждого заведения. ### Требования к query параметрам: 1. В `units` можно передать 1 з

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `fromDate` (string, required) — Дата начала периода в формате ISO 8601
- `toDate` (string, required) — Дата конца периода в формате ISO 8601
- `products` (string) — Список продуктов в формате UUID

Response (application/json):
- `result`: object
- `result.unitId`: string — Идентификатор заведения
- `result.productId`: string — Идентификатор продукта
- `result.productName`: string — Название продукта
- `result.productsCount`: integer — Количество продуктов
- `result.avgPending`: string — Среднее время в ожидании в формате HH:mm:ss
- `result.avgPacking`: string — Среднее время упаковки в формате HH:mm:ss
- `result.avgPreparing`: string — Среднее время подготовки в формате HH:mm:ss
- `result.avgTotal`: string — Общее среднее время в формате HH:mm:ss
- `result.stations`: array — Список станций и их среднее время
- `result.stations[].id`: string — Идентификатор станции
- `result.stations[].name`: string — Название станции
- `result.stations[].avgDuration`: string — Среднее время на станции в формате HH:mm:ss
- `isEndOfListReached`: boolean — Признак достижения конца списка

### Производство → Статистика выдачи заказов
**`GET /production/orders-handover-statistics`**
> Возвращает агрегированные данные по выдаче заказов за выбранный период по пиццериям (`units`). ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 1. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 1. В `salesChannels` следует перечислять каналы продаж (Delivery,DineIn,TakeAway) строго через запятую без пробелов; 1. Диапазон да

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `salesChannels` (string) — Фильтр по типу заказа Доставка/Ресторан/Самовывоз

Response (application/json):
- `ordersHandoverStatistics`: array — Статистика выдачи заказов
- `ordersHandoverStatistics[].unitId`: string — Идентификатор пиццерии
- `ordersHandoverStatistics[].unitName`: string — Название пиццерии в форматах Сыктывкар-1 или Москва 0-1
- `ordersHandoverStatistics[].avgTrackingPendingTime`: integer — Средняя время ожидания на трекинге в секундах с округлением
- `ordersHandoverStatistics[].avgCookingTime`: integer — Среднее время приготовления заказа в секундах с округлением
- `ordersHandoverStatistics[].avgHeatedShelfTime`: integer — Среднее время ожидания заказа на тепловой полке в секундах с округлением
- `ordersHandoverStatistics[].avgOrderAssemblyTime`: integer — Среднее время ожидания сборки заказа из приложения в ресторане в секундах с округлением. Заполняется если отчёт строится только для заказов типа `DineIn`, иначе
- `ordersHandoverStatistics[].avgOrderHandoverTime`: integer — Среднее время до выдачи заказа в секундах с округлением
- `ordersHandoverStatistics[].ordersCount`: integer — Количество заказов за запрашиваемый период с учётом исключений: в расчет не попадают заказы с временем приготовления выпекаемых продуктов меньше 1 минуты, заказ

### Производство → Производительность
**`GET /production/productivity`**
> Возвращает метрики производительности за выбранный период и указанным пиццериям (`units`). ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 1. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 1. Диапазон дат между `from` и `to` параметрами не должен превышать 31 день; 1. Даты `from` и `to` должны быть округлены до часов. Как 

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601 округлённого до часов
- `to` (string, required) — Конец периода в формате ISO 8601 округлённого до часов

Response (application/json):
- `productivityStatistics`: array — Статистика производительности по пиццериям
- `productivityStatistics[].unitId`: string — Идентификатор пиццерии
- `productivityStatistics[].unitName`: string — Название пиццерии в форматах Сыктывкар-1 или Москва 0-1
- `productivityStatistics[].laborHours`: number — Отработано часов
- `productivityStatistics[].sales`: number — Выручка
- `productivityStatistics[].salesPerLaborHour`: number — Выручка на человека в час
- `productivityStatistics[].productsPerLaborHour`: number — Продуктов на человека в час
- `productivityStatistics[].avgHeatedShelfTime`: integer — Время ожидания доставки на тепловой полке в секундах
- `productivityStatistics[].ordersPerCourierLabourHour`: number — Количество заказов на курьера в час

### Производство → Стоп-продажи по каналам продаж
**`GET /production/stop-sales-channels`**
> Возвращает данные о стопах продаж за период для набора пиццерий (`units`). Стопы сгруппированы по пиццериям и каналам продаж: доставка, самовывоз, ресторан. ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 3. Диапазон дат между `to` и `from` параметрами не должен превышать 3

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601

Response (application/json):
- `stopSalesBySalesChannels`: array — Данные о стопах продаж сгруппированные по заведениям и каналам продаж
- `stopSalesBySalesChannels[].id`: string — Id стопа
- `stopSalesBySalesChannels[].unitId`: string — Идентификатор пиццерии
- `stopSalesBySalesChannels[].unitName`: string — Название пиццерии в форматах Сыктывкар-1 или Москва 0-1
- `stopSalesBySalesChannels[].salesChannelName`: string — Канал продажи поставленный в стоп
- `stopSalesBySalesChannels[].reason`: string — Причина постановки в стоп
- `stopSalesBySalesChannels[].startedAtLocal`: string — Время начала стопа в формате ISO 8601 (локальное время)
- `stopSalesBySalesChannels[].endedAtLocal`: string — Время возобновления продаж в формате ISO 8601 (локальное время) (null - если еще не возобновлено)
- `stopSalesBySalesChannels[].stoppedByUserId`: string — Id сотрудника, который инициировал стоп продаж
- `stopSalesBySalesChannels[].resumedByUserId`: string — Id сотрудника, который возобновил продажи (null - если еще не возобновлено)
- `stopSalesBySalesChannels[].channelStopType`: string — Как были приостановлены продажи
- `stopSalesBySalesChannels[].startedAt`: string — Возвращает локальное время вместо UTC
- `stopSalesBySalesChannels[].endedAt`: string — Возвращает локальное время вместо UTC (null - если еще не возобновлено)

### Производство → Стоп-продажи по ингредиентам
**`GET /production/stop-sales-ingredients`**
> Возвращает данные о стопах продаж за период для набора пиццерий (`units`). Стопы сгруппированы по пиццериям и ингридиентам. ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 3. Диапазон дат между `to` и `from` параметрами не должен превышать 31 день. > #### Доступно для следу

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601

Response (application/json):
- `stopSalesByIngredients`: array — Данные о стопах продаж сгруппированные по заведениям и ингредиентам
- `stopSalesByIngredients[].id`: string — Id стопа
- `stopSalesByIngredients[].unitId`: string — Идентификатор пиццерии
- `stopSalesByIngredients[].unitName`: string — Название пиццерии в форматах Сыктывкар-1 или Москва 0-1
- `stopSalesByIngredients[].ingredientId`: string — Идентификатор ингредиента поставленного в стоп продажи
- `stopSalesByIngredients[].ingredientName`: string — наименование ингредиента поставленного в стоп продажи
- `stopSalesByIngredients[].ingredientCategoryName`: string — Категория ингредиента поставленного в стоп продажи
- `stopSalesByIngredients[].reason`: string — Причина постановки в стоп
- `stopSalesByIngredients[].startedAtLocal`: string — Время начала стопа в формате ISO 8601 (локальное время)
- `stopSalesByIngredients[].endedAtLocal`: string — Время возобновления продаж в формате ISO 8601 (локальное время) (null - если еще не возобновлено)
- `stopSalesByIngredients[].stoppeddByUserId`: string — Id пользователя, который инициировал стоп продаж
- `stopSalesByIngredients[].resumedByUserId`: string — Id пользователя, который возобновил продажи (null - если еще не возобновлено)
- `stopSalesByIngredients[].startedAt`: string — Возвращает локальное время вместо UTC
- `stopSalesByIngredients[].endedAt`: string — Возвращает локальное время вместо UTC (null - если еще не возобновлено)

### Производство → Стоп-продажи по продуктам
**`GET /production/stop-sales-products`**
> Возвращает данные о стопах продаж за период для набора пиццерий (`units`). Стопы сгруппированы по пиццериям и продуктам. ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 3. Диапазон дат между `to` и `from` параметрами не должен превышать 31 день. > #### Доступно для следующи

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601

Response (application/json):
- `stopSalesByProduct`: array — Данные о стопах продаж сгруппированные по заведениям и продуктам
- `stopSalesByProduct[].id`: string — Id стопа
- `stopSalesByProduct[].unitId`: string — Идентификатор пиццерии
- `stopSalesByProduct[].unitName`: string — Название пиццерии в форматах Сыктывкар-1 или Москва 0-1
- `stopSalesByProduct[].productId`: string — Идентификатор продукта
- `stopSalesByProduct[].productName`: string — Наименование продукта поставленного в стоп продажи
- `stopSalesByProduct[].productCategoryName`: string — Категория продукта поставленного в стоп продажи
- `stopSalesByProduct[].reason`: string — Причина постановки в стоп
- `stopSalesByProduct[].startedAtLocal`: string — Время начала стопа в формате ISO 8601 (локальное время)
- `stopSalesByProduct[].endedAtLocal`: string — Время возобновления продаж в формате ISO 8601 (локальное время) (null - если еще не возобновлено)
- `stopSalesByProduct[].stoppedByUserId`: string — Id пользователя, который инициировал стоп продаж
- `stopSalesByProduct[].resumedByUserId`: string — Id пользователя, который возобновил продажи (null - если еще не возобновлено)
- `stopSalesByProduct[].startedAt`: string — Возвращает локальное время вместо UTC
- `stopSalesByProduct[].endedAt`: string — Возвращает локальное время вместо UTC (null - если еще не возобновлено)

### Производство → Нагрузка на заведение по заказам
**`GET /production/unit-workload-by-orders`**
> Позволяет получить нагрузку по заказам на заведение за период в разрезе часа. В ответе будут включены только те промежутки, в которых производились заказы по заданному фильтру. Так например если заведение работает с 09:00 - 23:00, то в ответе будут только промежутки между 09:00 - 23:00. Любой отсутствующий промежуток можно считать за 0. ### Исключенные данные: Из выборки исключаются следующие данн

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `fromDate` (string, required) — Дата начала периода в формате ISO 8601
- `toDate` (string, required) — Дата конца периода в формате ISO 8601
- `salesChannels` (string) — Каналы продаж
- `productCategories` (string) — Категории продуктов
- `orderSources` (string) — Источники заказа
- `periodIntervalInMinutes` (integer) — Интервал просчета в минутах
- `products` (string) — Список продуктов в формате UUID
- `bakedOnly` (boolean) — Только выпекаемые продукты. Продукты проходящие через печь.

Response (application/json):
- `unitWorkload`: array
- `unitWorkload[].unitId`: string — Идентификатор заведения
- `unitWorkload[].fromLocal`: string — Начало периода
- `unitWorkload[].toLocal`: string — Окончание периода
- `unitWorkload[].ordersCount`: integer — Количество заказов за период
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Производство → Нагрузка на заведение по продуктам
**`GET /production/unit-workload-by-products`**
> Позволяет получить нагрузку по продуктам на заведение за период в разрезе часа. В ответе будут включены только те промежутки, в которых производились заказы по заданному фильтру. Так например если заведение работает с 09:00 - 23:00, то в ответе будут только промежутки между 09:00 - 23:00. Любой отсутствующий промежуток можно считать за 0. ### Исключенные данные: Из выборки исключаются следующие да

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `fromDate` (string, required) — Дата начала периода в формате ISO 8601
- `toDate` (string, required) — Дата конца периода в формате ISO 8601
- `salesChannels` (string) — Каналы продаж
- `productCategories` (string) — Категории продуктов
- `orderSources` (string) — Источники заказа
- `products` (string) — Список продуктов в формате UUID
- `bakedOnly` (boolean) — Только выпекаемые продукты. Продукты проходящие через печь.

Response (application/json):
- `unitWorkload`: array
- `unitWorkload[].unitId`: string — Идентификатор заведения
- `unitWorkload[].fromLocal`: string — Начало периода
- `unitWorkload[].toLocal`: string — Окончание периода
- `unitWorkload[].productsCount`: integer — Количество продуктов за период
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

## Команда

### Команда → Список сотрудников
**`GET /staff/members`**
> Возвращает список сотрудников, отсортированный по дате трудоустройства (`hiredOn`). ### Требования к query параметрам: - фильтр по типу должности `staffType` применяется только по одному значению - фильтр по статусу `statuses` применяется по нескольким значениям (указывается через запятую без пробелов) - фильтр по дате трудоустройства `hiredFrom/hiredTo`, применяется как по промежутку дат, так и п

Query:
- `staffType` (string) — Тип сотрудников
- `statuses` (string) — Состояние сотрудников. Можно указать несколько значений для поиска, перечислив их через запятую. Разрешенные значения: `Dismissed` - уволен, `Suspended` - отстр
- `hiredFrom` (string) — Фильтр по дате трудоустройства сотрудников, начало диапазона
- `hiredTo` (string) — Фильтр по дате трудоустройства сотрудников, конец диапазона
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `dismissedFrom` (string) — Фильтр по дате увольнения сотрудников, начало диапазона
- `dismissedTo` (string) — Фильтр по дате увольнения сотрудников, конец диапазона
- `units` (string) — Список заведений (пиццерий) Dodo IS в формате UUID
- `lastModifiedFrom` (string) — Фильтр по дате изменения, начало диапазона
- `lastModifiedTo` (string) — Фильтр по дате изменения сотрудников, конец диапазона

Response (application/json):
- `members`: array
- `members[].id`: string — Уникальный идентификатор сотрудника в формате UUID
- `members[].userId`: string — Уникальный идентификатор учетной записи сотрудника в формате UUID
- `members[].firstName`: string — Имя
- `members[].lastName`: string — Фамилия
- `members[].patronymicName`: ['string', 'null'] — Отчество
- `members[].sex`: string — Пол человека
- `members[].dateOfBirth`: string — Дата рождения сотрудника в формате ISO 8601
- `members[].phoneNumber`: string — Номер телефона в формате без символов (только цифры)
- `members[].taxpayerIdentificationNumber`: ['string', 'null'] — Код налогоплательщика (для РФ - ИНН)
- `members[].unitId`: string — Уникальный идентификатор заведения, к которому прикреплен сотрудник, в формате UUID
- `members[].unitName`: string — Название заведения, к которому прикреплен сотрудник
- `members[].staffType`: string — Тип сотрудника
- `members[].positionId`: ['string', 'null'] — Уникальный идентификатор должности в формате UUID. Отсутствует у типов сотрудника `Operator` и `PersonalManager`
- `members[].positionName`: ['string', 'null'] — Название должности. Отсутствует у типов сотрудника `Operator` и `PersonalManager`
- `members[].employmentTypeId`: string — Уникальный идентификатор типа трудоустройства в формате UUID
- `members[].employmentTypeName`: string — Название типа трудоустройства (ТК/ГПХ/самозанятый)
- `members[].status`: string — Статус. `Dismissed` - уволен, `Suspended` - отстранен, `Active` - работает
- `members[].hiredOn`: string — Дата трудоустройства в формате ISO 8601
- `members[].dismissedOn`: ['string', 'null'] — Дата увольнения в формате ISO 8601
- `members[].lastModifiedAt`: string — Дата и время последнего изменения информации о сотруднике в формате ISO 8601
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Команда → Смены сотрудников (по пиццериям)
**`GET /staff/shifts`**
> Смены сотрудников (рабочее время, рабочие часы, фактические часы): отработанное время с детализацией по дневным, ночным и праздничным часам (в минутах), данные о доставленных заказах, расстоянии, стаже сотрудника на момент смены. Смены выбираются по времени начала. Также есть фильтр по типу сотрудника. Время смен не обрезается по фильтру. Если начало смены попало в диапазон `clockInFrom` – `clockI

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `clockInFrom` (string, required) — Начало периода, в который попадает начало смен
- `clockInTo` (string, required) — Конец периода, в который попадает начало смен
- `staffTypeName` (string) — Фильтр по типу сотрудника
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `shifts`: array — Смены (рабочее время) сотрудников
- `shifts[].id`: string — Идентификатор смены
- `shifts[].staffId`: string — Идентификатор сотрудника
- `shifts[].clockInAtLocal`: string — Локальные дата и время начала смены в формате ISO 8601
- `shifts[].clockOutAtLocal`: string — Локальные дата и время окончания смены в формате ISO 8601
- `shifts[].staffPositionId`: string — Id должности сотрудника на смене
- `shifts[].staffPositionName`: string — Должность (категория) сотрудника на смене
- `shifts[].staffTypeName`: string — Тип сотрудника
- `shifts[].scheduleId`: string — Идентификатор запланированной смены. Принимает значение `null` для незапланированной смены и для смен до 04.04.2024
- `shifts[].seniority`: integer — Стаж сотрудника в месяцах
- `shifts[].deliveredOrdersCount`: integer — Количество доставленных заказов. Учитываются ВСЕ заказы, которые передавались в поездку, даже которые были отменены
- `shifts[].totalTripsDistance`: number — Расстояние всех поездок за смену в метрах
- `shifts[].totalTripsCount`: integer — Количество всех поездок за смену
- `shifts[].dayShiftMinutes`: integer — Дневное время смены в минутах
- `shifts[].nightShiftMinutes`: integer — Ночное время смены в минутах
- `shifts[].holidayMinutes`: integer — Сколько минут от праздничного дня попало в смену (из настроек Dodo IS)
- `shifts[].lastModifiedAt`: string — Дата и время последнего редактирования смены по UTC
- `shifts[].lastModifiedByUserId`: string — Идентификатор пользователя, который редактировал смену
- `shifts[].employmentTypeName`: string — Тип трудоустройства
- `shifts[].employmentTypeId`: string — Идентификатор типа трудоустройства
- `shifts[].unitId`: string — Идентификатор заведения (пиццерии)
- `shifts[].unitName`: string — Название заведения
- `shifts[].shiftUpdates`: array — Изменения в смене (рабочее время) сотрудника
- `shifts[].shiftUpdates[].clockIn`: string — Локальные дата и время начала смены в формате ISO 8601
- `shifts[].shiftUpdates[].clockOut`: string — Локальные дата и время окончания смены в формате ISO 8601
- `shifts[].shiftUpdates[].staffPositionId`: string — Id должности сотрудника на смене
- `shifts[].shiftUpdates[].staffPositionName`: string — Должность (категория) сотрудника на смене
- `shifts[].shiftUpdates[].staffTypeName`: string — Тип сотрудника
- `shifts[].shiftUpdates[].lastModifiedAt`: string — Дата и время последнего редактирования смены по UTC
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Команда → Смены сотрудников (по идентификаторам)
**`GET /staff/members/shifts`**
> Смены сотрудников (рабочее время, рабочие часы, фактические часы): отработанное время с детализацией по дневным, ночным и праздничным часам (в минутах), данные о доставленных заказах, расстоянии, стаже сотрудника на момент смены. Смены выбираются по времени начала. Также есть фильтр по типу сотрудника. Время смен не обрезается по фильтру. Если начало смены попало в диапазон `clockInFrom` – `clockI

Query:
- `staffIds` (string, required) — Идентификаторы сотрудников в формате UUID (разделитель запятая)
- `clockInFrom` (string, required) — Начало периода, в который попадает начало смен
- `clockInTo` (string, required) — Конец периода, в который попадает начало смен
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `shifts`: array — Смены (рабочее время) сотрудников
- `shifts[].id`: string — Идентификатор смены
- `shifts[].staffId`: string — Идентификатор сотрудника
- `shifts[].clockInAtLocal`: string — Локальные дата и время начала смены в формате ISO 8601
- `shifts[].clockOutAtLocal`: string — Локальные дата и время окончания смены в формате ISO 8601
- `shifts[].staffPositionId`: string — Id должности сотрудника на смене
- `shifts[].staffPositionName`: string — Должность (категория) сотрудника на смене
- `shifts[].staffTypeName`: string — Тип сотрудника
- `shifts[].scheduleId`: string — Идентификатор запланированной смены. Принимает значение `null` для незапланированной смены и для смен до 04.04.2024
- `shifts[].seniority`: integer — Стаж сотрудника в месяцах
- `shifts[].deliveredOrdersCount`: integer — Количество доставленных заказов. Учитываются ВСЕ заказы, которые передавались в поездку, даже которые были отменены
- `shifts[].totalTripsDistance`: number — Расстояние всех поездок за смену в метрах
- `shifts[].totalTripsCount`: integer — Количество всех поездок за смену
- `shifts[].dayShiftMinutes`: integer — Дневное время смены в минутах
- `shifts[].nightShiftMinutes`: integer — Ночное время смены в минутах
- `shifts[].holidayMinutes`: integer — Сколько минут от праздничного дня попало в смену (из настроек Dodo IS)
- `shifts[].lastModifiedAt`: string — Дата и время последнего редактирования смены по UTC
- `shifts[].lastModifiedByUserId`: string — Идентификатор пользователя, который редактировал смену
- `shifts[].employmentTypeName`: string — Тип трудоустройства
- `shifts[].employmentTypeId`: string — Идентификатор типа трудоустройства
- `shifts[].unitId`: string — Идентификатор заведения (пиццерии)
- `shifts[].unitName`: string — Название заведения
- `shifts[].shiftUpdates`: array — Изменения в смене (рабочее время) сотрудника
- `shifts[].shiftUpdates[].clockIn`: string — Локальные дата и время начала смены в формате ISO 8601
- `shifts[].shiftUpdates[].clockOut`: string — Локальные дата и время окончания смены в формате ISO 8601
- `shifts[].shiftUpdates[].staffPositionId`: string — Id должности сотрудника на смене
- `shifts[].shiftUpdates[].staffPositionName`: string — Должность (категория) сотрудника на смене
- `shifts[].shiftUpdates[].staffTypeName`: string — Тип сотрудника
- `shifts[].shiftUpdates[].lastModifiedAt`: string — Дата и время последнего редактирования смены по UTC
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Команда → Курьеры на смене
**`GET /staff/couriers-on-shift`**
> Возвращает список курьеров на смене на момент отправки запроса. ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 3. Дата `on` должна быть в прошлом. > #### Доступно для следующих ролей: > > `Division administrator` - Администратор подразделения > > `Store Manager` - Менеджер

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `on` (string) — Дата и время для просмотра списка курьеров на смене в заданный момент. Если оставить параметр пустым, вернется информация на текущий момент.

Response (application/json):
- `couriers`: array — Курьеры на смене
- `couriers[].id`: string — Идентификатор сотрудника
- `couriers[].clockInAt`: string — Дата и время начала смены
- `couriers[].clockInAtLocal`: string — Локальные дата и время начала смены
- `couriers[].scheduledClockOutAt`: string — Дата и время планируемого окончания смены
- `couriers[].scheduledClockOutAtLocal`: string — Локальные дата и время планируемого окончания смены
- `couriers[].positionId`: string — Идентификатор должности сотрудника на смене
- `couriers[].positionName`: string — Должность сотрудника на смене
- `couriers[].scheduleId`: string — Идентификатор запланированной смены. Принимает значение `null` для незапланированной смены и для смен до 04.04.2024
- `couriers[].unitId`: string — Идентификатор заведения (пиццерии)
- `couriers[].unitName`: string — Название заведения
- `couriers[].deliveredOrdersCount`: integer — Количество доставленных заказов
- `couriers[].lateOrdersCount`: integer — Количество доставленных заказов c опозданием
- `couriers[].cashFromOrders`: number — Наличные от заказов

### Команда → Расписания
**`GET /staff/schedules`**
> Расписания смен сотрудников Период выборки учитывает только начало смены в расписании, то есть конец смены может выходить за пределы периода выборки. > #### Доступно для следующих ролей: > > `Division administrator` - Администратор подразделения > > `Store Manager` - Менеджер офиса > > `Shift supervisor` - Менеджер смены

Query:
- `beginFrom` (string, required) — Начало периода (в формате ISO 8601), в который попадает начало смен
- `beginTo` (string, required) — Конец периода (в формате ISO 8601), в который попадает начало смен
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `staffType` (string) — Фильтр по типу сотрудника

Response (application/json):
- `schedules`: array
- `schedules[].id`: string — Идентификатор запланированной смены
- `schedules[].scheduledShiftEndAtLocal`: string — Запланированный конец смены (локальное время)
- `schedules[].scheduledShiftStartAtLocal`: string — Запланированное начало смены (локальное время)
- `schedules[].workStationId`: string — Идентификатор производственной станции
- `schedules[].workStationName`: string — Производственная станция (Доставка, Кухня, Касса)
- `schedules[].workSubStationName`: string — Производственная подстанция (Холодный цех, Чистота и тд)
- `schedules[].staffPositionId`: string — Идентификатор текущей должности сотрудника на данный момент (даже если расписание за прошлые периоды)
- `schedules[].staffId`: string — Идентификатор сотрудника
- `schedules[].staffPositionName`: string — Текущая должность сотрудника на данный момент (даже если расписание за прошлые периоды)
- `schedules[].staffShiftPositionId`: string — Идентификатор должности сотрудника на смене, если она отличалась от текущей
- `schedules[].staffShiftPositionName`: string — Должность сотрудника на смене, если она отличалась от текущей
- `schedules[].staffTypeName`: string — Тип сотрудника
- `schedules[].unitId`: string — Идентификатор заведения
- `schedules[].unitName`: string — Название заведения
- `schedules[].modifiedAt`: string — Дата и время последнего изменения
- `schedules[].modifiedByUserId`: string — Идентификатор пользователя, редактировавшего запланированную смену
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Команда → Расписания: прогнозные метрики
**`GET /staff/schedules/forecast`**
> Возвращает прогнозные метрики расписания сотрудников (графики) по заведениям на указанную дату, включая прогноз выручки, количества заказов и товаров, рекомендуемую и запланированную нагрузку сотрудников, а также почасовой прогноз по заведению и курьерам. Метрики строятся только для заведений (UnitType = Store). ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в од

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `on` (string, required) — Дата постороения прогноза графиков смен в формате ISO 8601

Response (application/json):
- `metrics`: array
- `metrics[].unitId`: string — Идентификатор заведения
- `metrics[].forecastDate`: string — Дата построения прогноза в формате ISO 8601
- `metrics[].forecastRevenue`: number — Прогнозная выручка на день в рублях
- `metrics[].forecastProductsCount`: number — Прогнозное количество приготавливаемых продуктов на день
- `metrics[].forecastCourierOrdersCount`: number — Прогнозное количество заказов на доставку за день
- `metrics[].forecastRevenueProductivity`: number — Прогнозная производительность в выручке на человека в час.
- `metrics[].forecastProductsProductivity`: number — Прогнозная производительность в продуктах на человека в час.
- `metrics[].forecastCourierProductivity`: number — Прогнозная производительность в заказах на курьера в час.
- `metrics[].scheduledUnitLaborMinutes`: integer — Назначено рабочих минут всех сотрудников заведения (пиццерии). Число кратно 15-ти.
- `metrics[].scheduledCourierLaborMinutes`: integer — Назначено рабочих минут курьеров. Число кратно 15-ти.
- `metrics[].goalRevenueProductivity`: number — Целевая производительность в выручке на человека в час.
- `metrics[].goalProductsProductivity`: number — Целевая производительность в продуктах на человека в час.
- `metrics[].goalCourierProductivity`: number — Целевая производительность в заказах на курьера в час.
- `metrics[].forecastByHour`: array — Детализация прогноза по часам
- `metrics[].forecastByHour[].forecastForHourLocal`: integer — час
- `metrics[].forecastByHour[].forecastRevenue`: number — Прогнозная выручка за час в рублях
- `metrics[].forecastByHour[].forecastProductsCount`: number — Прогнозное количество приготавливаемых продуктов за час
- `metrics[].forecastByHour[].forecastCourierOrdersCount`: number — Прогнозное количество заказов на доставку за час
- `metrics[].forecastByHour[].recommendedUnitLaborMinutes`: integer — Рекомендованно рабочих минут сотрудников заведения. Число кратно 15-ти.
- `metrics[].forecastByHour[].recommendedCourierLaborMinutes`: integer — Рекомендованно рабочих минут курьеров. Число кратно 15-ти.
- `metrics[].forecastByHour[].scheduledUnitLaborMinutes`: integer — Запланированно рабочих минут сотрудников заведения. Число кратно 15-ти.
- `metrics[].forecastByHour[].scheduledCourierLaborMinutes`: integer — Запланированно рабочих минут курьеров. Число кратно 15-ти.

### Команда → Должности сотрудников
**`GET /staff/positions`**
> Полный список активных должностей сотрудников в системе.

Response (application/json):
- `positions`: array
- `positions[].id`: string — Идентификатор должности сотрудника на смене
- `positions[].name`: string — Наименование должности
- `positions[].staffTypeName`: string — Тип сотрудника

### Команда → Вознаграждения (новое)
**`GET /staff/incentives-by-members`**
> Возвращает вознаграждения за период для набора заведений (`units`). Вознаграждения сгруппированы по сотруднику с детализацией: смены, премии. Вознаграждения считаются для сотрудников, которые отработали хотя бы одну смену в указанных заведения за указанный период времени, а также сведения о премии. Из-за возможного изменения ставки и коэффициентов в течение времени смены, она разбивается на интерв

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `staffTypes` (string) — Фильтр по типу сотрудника

Response (application/json):
- `staffMembers`: array — Вознаграждения по сотрудникам
- `staffMembers[].staffId`: string — Идентификатор сотрудника
- `staffMembers[].phoneNumber`: string — Номер телефона сотрудника
- `staffMembers[].taxpayerIdentificationNumber`: string — Код налогоплательщика сотрудника (для РФ - ИНН)
- `staffMembers[].shiftsDetailing`: array — Детализация вознаграждений по сменам
- `staffMembers[].shiftsDetailing[].shiftId`: string — Идентификатор смены
- `staffMembers[].shiftsDetailing[].unitId`: string — Идентификатор заведения
- `staffMembers[].shiftsDetailing[].staffType`: string — Тип сотрудника
- `staffMembers[].shiftsDetailing[].positionId`: string
- `staffMembers[].shiftsDetailing[].positionName`: string — Должность (категория) сотрудника на смене
- `staffMembers[].shiftsDetailing[].employmentTypeId`: string — Идентификатор типа трудоустройства
- `staffMembers[].shiftsDetailing[].EmploymentTypeName`: string
- `staffMembers[].shiftsDetailing[].clockInAtLocal`: string — Дата и время начала смены в формате ISO 8601 (локальное время)
- `staffMembers[].shiftsDetailing[].clockOutAtLocal`: string — Дата и время окончания смены в формате ISO 8601 (локальное время)
- `staffMembers[].shiftsDetailing[].nightShiftWage`: number — Вознаграждение за работу в ночные часы
- `staffMembers[].shiftsDetailing[].dayShiftWage`: number — Вознаграждение за работу в дневные часы
- `staffMembers[].shiftsDetailing[].ordersWage`: number — Вознаграждение за доставленные заказы
- `staffMembers[].shiftsDetailing[].tripsDistanceWage`: number — Вознаграждение за пройденное расстояние
- `staffMembers[].shiftsDetailing[].tripsCountWage`: number — Вознаграждение за количество поездок
- `staffMembers[].shiftsDetailing[].shiftPremiums`: number — Сумма премий менеджера смены
- `staffMembers[].shiftsDetailing[].shiftPremiumsComment`: string — Комментарии к премиям менеджера смены
- `staffMembers[].shiftsDetailing[].seniorityBonus`: number — Вознаграждение за стаж
- `staffMembers[].shiftsDetailing[].totalWage`: number — Итоговое вознаграждение за смену
- `staffMembers[].premiums`: array — Премии, назначенные вне смен (через Менеджер Офиса)
- `staffMembers[].premiums[].id`: string — Идентификатор премии
- `staffMembers[].premiums[].unitId`: string — Идентификатор заведения
- `staffMembers[].premiums[].amount`: number — Размер премии
- `staffMembers[].premiums[].atLocal`: string — Дата и время назначения в формате ISO 8601 (локальное время)
- `staffMembers[].premiums[].comment`: string — Комментарий
- `staffMembers[].premiums[].at`: string — Возвращает локальное время вместо UTC
- `staffMembers[].totalIncentives`: number — Итоговое вознагражление для сотрудника за период

### Команда → Премии
**`GET /staff/incentives/premium`**
> Метод позволяет получить список установленных премий сотрудникам в указанных заведениях за определённый период. ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 3. В ответе будут отображены все премии, которые попадают в запрашиваемый период `from`-`to` полностью или частичн

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `staffMembers` (string) — 
- `staffPositions` (string) — 
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `premiums`: array
- `premiums[].unitId`: string — Идентификатор заведения
- `premiums[].staffId`: string — Идентификатор сотрудника
- `premiums[].staffPositionId`: ['string', 'null'] — Идентификатор должности
- `premiums[].fromLocal`: string — Дата начала премии в локальном времени
- `premiums[].toLocal`: string — Дата окончания премии в локальном времени
- `premiums[].premiumType`: string — Тип премии
- `premiums[].amount`: number — Значение премии
- `premiums[].comment`: ['string', 'null'] — Комментарий к премии
- `premiums[].createdAtLocal`: string — Дата и время создания премии в локальном времени
- `premiums[].createdBy`: string — Идентификатор пользователя добавившего премию
- `premiums[].modifiedAt`: string — Дата и время последнего изменения премии (UTC)
- `premiums[].modifiedBy`: ['string', 'null'] — Идентификатор пользователя, внесшего последнее изменение
- `premiums[].modifiedAtLocal`: string — Возвращает UTC вместо локального времени
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Команда → Количество открытых вакансий
**`GET /staff/vacancies/count`**
> Получение количества открытых вакансий в заведениях ### Требования к входным параметрам: - В `units` можно перечислить до 30 заведений в одном запросе; - В `units` следует перечислять UUID-ы строго через запятую без пробелов; - В `localities` можно перечислить до 30 населенных пунктов в одном запросе; - В `localities` следует перечислять UUID-ы строго через запятую без пробелов; - Можно передавать

Query:
- `localities` (string) — Список населенных пунктов в формате UUID
- `units` (string) — Список заведений (пиццерий/кофеен) Dodo IS в формате UUID
- `take` (integer) — Количество записей, которые следует выбрать
- `skip` (integer) — Количество записей, которые следует пропустить
- `countryId` (?) — Идентификатор страны
- `businessId` (string) — Идентификатор бизнеса

Response (application/json):
- `vacancies`: array
- `vacancies[].id`: string — Идентификатор заведения
- `vacancies[].name`: string — Название заведения
- `vacancies[].address`: ['string', 'null'] — Адрес заведения
- `vacancies[].unitLocalityId`: ['string', 'null'] — Идентификатор населённого пункта, в котором находится заведение
- `vacancies[].vacanciesCount`: number — Количество вакансий в заведении
- `vacancies[].location`: ['object', 'null'] — Координаты заведения. Null возвращется, если координаты заведения не заведены в системе или имеют невалидный формат.
- `vacancies[].metroStations`: array — Список станций метро связанных с заведением
- `vacancies[].metroStations[].name`: string — Название стацнии метро
- `vacancies[].countryId`: string — Идентификатор страны в формате ISO 3166-1 alpha-2
- `vacancies[].businessId`: string — Идентификатор бизнеса в формате UUID
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Команда → Открытые вакансии
**`GET /staff/vacancies`**
> Получение открытых вакансий в заведениях ### Требования к входным параметрам: - В `units` можно перечислить до 30 заведений в одном запросе; - В `units` следует перечислять UUID-ы строго через запятую без пробелов; - В `localities` можно перечислить до 30 населенных пунктов в одном запросе; - В `localities` следует перечислять UUID-ы строго через запятую без пробелов; - Можно передавать либо парам

Query:
- `localities` (string) — Список населённых пунктов в формате UUID
- `units` (string) — Список заведений (пиццерий/кофеен) Dodo IS в формате UUID
- `take` (integer) — Количество записей, которые следует выбрать
- `skip` (integer) — Количество записей, которые следует пропустить
- `staffTypes` (string) — Фильтр по типу сотрудника
- `countryId` (?) — Идентификатор страны
- `businessId` (string) — Идентификатор бизнеса

Response (application/json):
- `vacancies`: array
- `vacancies[].unit`: object — Информация о заведении
- `vacancies[].unit.unitId`: string — Идентификатор заведения
- `vacancies[].unit.unitName`: string — Название заведения
- `vacancies[].unit.address`: object — Информация о местонахождении заведения
- `vacancies[].unit.address.addressString`: ['string', 'null'] — Полный адрес заведения
- `vacancies[].unit.address.unitLocalityId`: ['string', 'null'] — Идентификатор города или населённого пункта, где находится заведение
- `vacancies[].unit.address.unitLocalityName`: ['string', 'null'] — Название города или населённого пункта, где находится заведение
- `vacancies[].unit.address.street`: ['string', 'null'] — Улица, на которой находится заведение
- `vacancies[].unit.address.house`: ['string', 'null'] — Номер дома, в котором находится заведение
- `vacancies[].unit.address.location`: ['object', 'null'] — Координаты заведения. Значение Null возвращается, если координаты отсутствуют в системе или указаны в неверном формате.
- `vacancies[].unit.address.metroStations`: array — Перечень станций метро, связанных с заведением
- `vacancies[].unit.address.metroStations[].name`: string — Название стацнии метро
- `vacancies[].id`: string — Идентификатор вакансии
- `vacancies[].positionName`: string — Наименование должности
- `vacancies[].vehicleOwnershipType`: string — Указывает, кому принадлежит транспортное средство: компании (Company), сотруднику (Personal), третьей стороне (ThirdParty) или не используется (None). Признак х
- `vacancies[].staffTypeName`: string — Тип сотрудника
- `vacancies[].incentive`: ['number', 'null'] — Вознаграждение за месяц до вычета
- `vacancies[].incentiveAfterTax`: ['number', 'null'] — Вознаграждение за месяц на руки
- `vacancies[].hourlyRate`: ['number', 'null'] — Почасовая ставка до вычета
- `vacancies[].monthlyWorkingHours`: ['integer', 'null'] — Количество рабочих часов за месяц
- `vacancies[].hasBonus`: ['boolean', 'null'] — Предусмотрена ли какая-либо премия для сотрудника по данной вакансии
- `vacancies[].hourlyBonus`: ['number', 'null'] — Почасовая премия до вычета. Это поле может содержать размер премии за каждый отработанный час при выполнении определенного условия сотрудником
- `vacancies[].conditionToGetBonus`: ['string', 'null'] — Условия получения почасовой премии. : параметры hourlyBonus и conditionToGetBonus либо оба должны быть заполнены, либо оба равны null
- `vacancies[].otherBonus`: ['number', 'null'] — Прочие виды премий. В отличие от почасовой премии, это поле используется для указания любой премии, не связанной с временным интервалом. Параметры otherBonus и 
- `vacancies[].conditionToGetOtherBonus`: ['string', 'null'] — Условия получения прочих видов премий: параметры otherBonus и conditionToGetOtherBonus либо оба должны быть заполнены, либо оба равны null
- `vacancies[].hasFuelReimbursement`: ['boolean', 'null'] — Предусмотрена ли компенсация за горюче-смазочные материалы. Этот параметр актуален только для вакансий курьера
- `vacancies[].fuelReimbursementRatePerKilometer`: ['number', 'null'] — Оплата за использование горюче-смазочных материалов (ГСМ) за километр до вычета. Если значение составляет 0, компенсация ГСМ не предусмотрена. Этот параметр акт
- `vacancies[].ratePerOrder`: ['number', 'null'] — Ставка за заказ до вычета. Если указано значение 0, оплата за заказ не предусмотрена. Этот параметр актуален только для вакансий на должность курьера
- `vacancies[].amortizationPaymentPerKilometer`: ['number', 'null'] — Оплата амортизации за километр до вычета. При значении 0 оплата амортизации не предусмотрена. Этот параметр актуален только для вакансий на должность курьера
- `vacancies[].ratePerTrip`: ['number', 'null'] — Ставка за поездку до вычета. При значении 0 оплата за поездку не предусмотрена. Этот параметр актуален только для вакансий на должность курьера
- `vacancies[].countryId`: string — Идентификатор страны в формате ISO 3166-1 alpha-2
- `vacancies[].businessId`: string — Идентификатор бизнеса в формате UUID
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Команда → Информация о сотруднике
**`GET /staff/members/{id}`**
> Возвращает полную информацию по запрошенному сотруднику. Вы можете запросить информацию о сотруднике как по идентификатору сотрудника (`staffId`), так и по идентификатору пользователя (`userId`). Для этого необходимо указать дополнительный параметр `findby`. Данный параметр принимает 2 значения: - `staffid` - для поиска по идентификатору сотрудника (используется как стандартное значение) - `userid

Query:
- `findby` (string) — Способ поиска сотрудника.

Response (application/json):
- `id`: string — Уникальный идентификатор сотрудника в формате UUID
- `userId`: string — Уникальный идентификатор учетной записи сотрудника в формате UUID
- `firstName`: string — Имя
- `lastName`: string — Фамилия
- `patronymicName`: ['string', 'null'] — Отчество
- `sex`: string — Пол человека
- `businessId`: string — Название бизнеса (концепции), к которому прикреплен сотрудник
- `countryCode`: string — Код страны, к которой прикреплен сотрудник, в формате ISO 3166-1 alpha-2
- `status`: string — Статус. `Dismissed` - уволен, `Suspended` - отстранен, `Active` - работает
- `dismissalReason`: ['string', 'null'] — Причина увольнения(причины зафиксированы в системе)
- `dismissalComment`: ['string', 'null'] — Комментарий к причине увольнения
- `dateOfBirth`: string — Дата рождения сотрудника в формате ISO 8601
- `hiredOn`: string — Дата трудоустройства в формате ISO 8601
- `dismissedOn`: ['string', 'null'] — Дата увольнения в формате ISO 8601
- `lastModifiedAt`: string — Дата и время последнего изменения информации о сотруднике в формате ISO 8601
- `phoneNumber`: string — Номер телефона в формате без символов (только цифры)
- `email`: ['string', 'null'] — Адрес электронной почты
- `taxpayerIdentificationNumber`: ['string', 'null'] — Код налогоплательщика (для РФ - ИНН)
- `unitId`: string — Уникальный идентификатор заведения, к которому прикреплен сотрудник, в формате UUID
- `unitName`: string — Название заведения, к которому прикреплен сотрудник
- `staffType`: string — Тип сотрудника
- `positionId`: ['string', 'null'] — Уникальный идентификатор должности в формате UUID. Отсутствует у типов сотрудника `Operator` и `PersonalManager`
- `positionName`: ['string', 'null'] — Название должности. Отсутствует у типов сотрудника `Operator` и `PersonalManager`
- `employmentTypeId`: string — Уникальный идентификатор типа трудоустройства в формате UUID
- `employmentTypeName`: string — Название типа трудоустройства (ТК/ГПХ/самозанятый)
- `isHealthPermitAvailable`: boolean — Признак, показывающий наличие (true) или отсутствие (false) у сотрудника медицинской книжки
- `healthPermitIssuedOn`: ['string', 'null'] — Дата выдачи медицинской книжки в формате ISO 8601
- `healthPermitExpiresOn`: ['string', 'null'] — Дата окончания действия медициского осмотра в медицинской книжке в формате ISO 8601
- `healthPermitValidUntil`: ['string', 'null'] — Дата окончания действия медицинской книжки в формате ISO 8601

### Команда → Поиск сотрудников
**`GET /staff/members/search`**
> Возвращает список сотрудников с данными о трудоустройстве. Для поиска используется алгоритм, который описан ниже, а результирующие персональные данные скрываются. ### ВАЖНО: Алгоритм рассматривает только указанные ниже группы совпадений! В результирующий список попадут все найденные записи, удовлетворяющие нижеперечисленным группам совпадения. ## Группировка запросов для алгоритма поиска ### 6 сов

Query:
- `firstName` (['string', 'null']) — Имя
- `lastName` (['string', 'null']) — Фамилия
- `patronymicName` (['string', 'null']) — Отчество
- `taxpayerIdentificationNumber` (['string', 'null']) — ИНН
- `phoneNumber` (['string', 'null']) — Телефон. Если в телефоне есть символ '+' необходимо передавать код символа '%2B'. Например '+7' -> '%2B7'.
- `dateOfBirth` (['string', 'null']) — Дата рождения

Response (application/json):
- `staffMatches`: array
- `staffMatches[].firstName`: string — Имя, маскируется до 3 символов, если не передано в качестве параметра на поиск
- `staffMatches[].lastName`: string — Фамилия, маскируется до 3 символов если не передано в качестве параметра на поиск
- `staffMatches[].patronymicName`: string — Отчество, маскируется до 3 символов если не передано в качестве параметра на поиск
- `staffMatches[].taxpayerIdentificationNumber`: string — ИНН, маскируется полностью если не передано в качестве параметра на поиск
- `staffMatches[].phoneNumber`: string — Телефон, маскируется до 4 символов если не передано в качестве параметра на поиск
- `staffMatches[].dateOfBirth`: string — Дата рождения, маскируются первые 4 символа если не передано в качестве параметра на поиск
- `staffMatches[].hiredOn`: string — Дата найма
- `staffMatches[].dismissedOn`: string — Дата увольнения
- `staffMatches[].positionName`: string — Должность
- `staffMatches[].positionId`: string — Идентификатор должности
- `staffMatches[].status`: string — Состояние сотрудника `Dismissed` - уволен, `Suspended` - отстранен, `Active` - работает
- `staffMatches[].dismissalReason`: string — Причина увольнения
- `staffMatches[].dismissalComment`: string — Комментарий к причине увольнения
- `staffMatches[].unitId`: string — Идентификатор юнита
- `staffMatches[].unitName`: string — Юнит
- `staffMatches[].matchesCount`: integer
- `staffMatches[].matches`: object
- `staffMatches[].matches.isFirstNameMatched`: boolean
- `staffMatches[].matches.isLastNameMatched`: boolean
- `staffMatches[].matches.isPatronymicNameMatched`: boolean
- `staffMatches[].matches.isTaxpayerIdentificationNumberMatched`: boolean
- `staffMatches[].matches.isPhoneNumberMatched`: boolean
- `staffMatches[].matches.isDateOfBirthMatched`: boolean

## Учёт

### Учёт → Продукты
**`GET /accounting/products`**
> Возвращает список продуктов, отсортированный по ID. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). <b>ВНИМАНИЕ:</b> параметры `takenCount` и `totalCount` скоро будут удалены!

Query:
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `isProducible` (boolean) — Флаг, по которому фильтруются производимые продукты
- `modifiedAt` (string) — Дата и время изменения в формате ISO 8601. Фильтр возвращает записи у которых дата изменения больше или равна переданной
- `excludeInactive` (boolean) — Следует ли исключать неактивные продукты из результирующей выборки
- `includeRemoved` (boolean) — Следует ли включать удаленные продукты в результирующую выборку

Response (application/json):
- `products`: array
- `products[].id`: string — Идентификатор продукта
- `products[].isProducible`: boolean — Производимый (false - товар, true - продукт, который производим сами)
- `products[].defaultName`: string — Название, построенное с учётом признаков продукта
- `products[].name`: string — Название, без учёта признаков продукта
- `products[].measurementValue`: number — Значение измерения
- `products[].measurementUnit`: string — Единица измерения продукта
- `products[].measurementGroup`: string — Группа измерения
- `products[].doughType`: string — Тип теста
- `products[].modifiedAt`: string — Дата и время изменения продукта в формате ISO 8601
- `products[].stockItems`: array
- `products[].stockItems[].id`: string — Идентификатор сырья
- `products[].stockItems[].name`: string — Название сырья
- `takenCount`: integer — Получено записей
- `totalCount`: integer — Всего записей
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Информация о продукте
**`GET /accounting/products/{id}`**
> Возвращает информацию о продукте

Response (application/json):
- `id`: string — Идентификатор продукта
- `isProducible`: boolean — Производимый (false - товар, true - продукт, который производим сами)
- `defaultName`: string — Название, построенное с учётом признаков продукта
- `name`: string — Название, без учёта признаков продукта
- `measurementValue`: number — Значение измерения
- `measurementUnit`: string — Единица измерения продукта
- `measurementGroup`: ['string', 'null'] — Группа измерения
- `doughType`: ['string', 'null'] — Тип теста
- `modifiedAt`: string — Дата и время изменения продукта в формате ISO 8601
- `stockItems`: array
- `stockItems[].id`: string — Идентификатор сырья
- `stockItems[].name`: string — Название сырья

### Учёт → Сырьё
**`GET /accounting/stock-items`**
> ⚠️ **DEPRECATED** - данный endpoint устарел. Пожалуйста, используйте новый Accounting API [**Справочники → Сырьё**](https://docs.dodois.io/docs/accounting/3fe4c4a59cf7e-spravochniki-syryo) Возвращает список сырья, отсортированный по ID. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец спи

Query:
- `modifiedAt` (string) — Дата и время изменения в формате ISO 8601. Фильтр возвращает записи у которых дата изменения больше или равна переданной
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `stockItems`: array — Сырье
- `stockItems[].id`: string — Идентификатор сырья
- `stockItems[].name`: string — Название сырья
- `stockItems[].measurementUnit`: string — Наименование единицы измерения
- `stockItems[].categoryName`: string — Категория сырья
- `stockItems[].modifiedAt`: string — Дата и время изменения в формате ISO 8601
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Списанные продукты
**`GET /accounting/write-offs/products`**
> Возвращает списанные продукты за указанный период (включительно), отсортированные по дате списания. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. 

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `writeOffs`: array
- `writeOffs[].unitId`: string — Идентификатор заведения
- `writeOffs[].unitName`: string — Название заведения
- `writeOffs[].writtenOffAtLocal`: string — Дата и время списания (локальное время)
- `writeOffs[].reason`: string — Причина списания. **Доступность причин по бизнесам:** **Dodo Pizza:** - `Expired` - Истёк срок годности - `Defected` - Брак - `DamagedPackaging` - Повреждённая 
- `writeOffs[].productId`: string — Идентификатор продукта
- `writeOffs[].productName`: string — Наименование продукта
- `writeOffs[].quantity`: number — Списанное количество
- `writeOffs[].pricePerPiece`: number — Цена без скидки за 1 шт. в формате #.##
- `writeOffs[].stockItems`: array
- `writeOffs[].stockItems[].id`: string — Идентификатор сырья
- `writeOffs[].stockItems[].name`: string — Название сырья
- `writeOffs[].stockItems[].quantity`: number — Списанное количество в формате #.###
- `writeOffs[].stockItems[].measurementUnit`: string — Наименование единицы измерения
- `writeOffs[].writtenOffAt`: string — Дата и время списания (Возвращает локальное время вместо UTC)
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Списанное сырьё
**`GET /accounting/write-offs/stock-items`**
> Возвращает списанное сырье за указанный период (включительно), отсортированное по дате списания. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `writeOffs`: array — Список записей о списаниях сырья
- `writeOffs[].unitId`: string — Идентификатор заведения
- `writeOffs[].unitName`: string — Название заведения
- `writeOffs[].writtenOffAtLocal`: string — Дата и время списания (локальное время)
- `writeOffs[].stockItemId`: string — Идентификатор сырья
- `writeOffs[].stockItemName`: string — Наименование списанного сырья
- `writeOffs[].quantity`: number — Списанное количество
- `writeOffs[].measurementUnit`: string — Наименование единицы измерения
- `writeOffs[].reason`: string — Причина списания. **Доступность причин по бизнесам:** **Dodo Pizza:** - `Expired` - Истёк срок годности - `Defected` - Брак - `DamagedPackaging` - Повреждённая 
- `writeOffs[].writtenOffAt`: string — Дата и время списания (Возвращает локальное время вместо UTC)
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Забракованные продукты
**`GET /accounting/defective-products`**
> Возвращает забракованные продукты за указанный период (включительно), отсортированные по дате и идентификатору продажи. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений 

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `defects`: array
- `defects[].unitId`: string — Идентификатор заведения
- `defects[].unitName`: string — Название заведения
- `defects[].productId`: string — Идентификатор продукта
- `defects[].productName`: string — Наименование продукта
- `defects[].soldAtLocal`: string — Дата и время продажи в формате ISO 8601 (локальное время)
- `defects[].priceWithDiscount`: number — Цена со скидкой в формате #.##
- `defects[].addedIngredients`: array
- `defects[].addedIngredients[].id`: string — Идентификатор добавленного ингредиента
- `defects[].addedIngredients[].name`: string — Название добавленного ингредиента
- `defects[].addedIngredients[].price`: number — Цена добавленного ингредиента в формате #.##
- `defects[].pizzaHalves`: array
- `defects[].pizzaHalves[].id`: string — Идентификатор пиццы половинки
- `defects[].pizzaHalves[].name`: string — Название пиццы половинки
- `defects[].pizzaHalves[].price`: number — Цена пиццы половинки в формате #.##
- `defects[].soldAt`: string — Дата и время продажи в формате ISO 8601 (Возвращает локальное время вместо UTC)
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Питание персонала
**`GET /accounting/staff-meals`**
> Возвращает продукты за указанный период (включительно), израсходованные на питание персонала, отсортированные по дате принятия заказа и идентификатору продажи. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам: 1. В `u

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `staffMeals`: array
- `staffMeals[].unitId`: string — Идентификатор заведения
- `staffMeals[].unitName`: string — Название заведения
- `staffMeals[].staffId`: string — Идентификатор сотрудника
- `staffMeals[].orderAcceptedAtLocal`: string — Дата и время принятия заказа в формате ISO 8601 (локальное время)
- `staffMeals[].productId`: string — Идентификатор продукта
- `staffMeals[].productName`: string — Наименование продукта
- `staffMeals[].price`: number — Цена без скидки в формате #.##
- `staffMeals[].orderAcceptedAt`: string — Дата и время принятия заказа в формате ISO 8601 (Возвращает локальное время вместо UTC)
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Отмены заказов
**`GET /accounting/cancelled-sales`**
> Возвращает продукты за указанный период (включительно), израсходованные на отмены заказов, отсортированные по дате, идентификатору продажи и идентификатору записи. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам: 1. 

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `cancelledSales`: array
- `cancelledSales[].orderId`: string — Идентификатор заказа
- `cancelledSales[].soldAtLocal`: string — Дата и время заказа (локальное время)
- `cancelledSales[].clientId`: string — Идентификатор клиента
- `cancelledSales[].paymentMethod`: string — Метод оплаты
- `cancelledSales[].paymentProviderName`: string — Название провайдера, который провёл оплату
- `cancelledSales[].orderSource`: string — Источник заказа
- `cancelledSales[].checkNumber`: integer — Номер чека продажи, который выдал ККМ, показывает был ли распечатан чек для заказа
- `cancelledSales[].salesChannel`: string — Канал продаж заказа
- `cancelledSales[].unitId`: string — Идентификатор заведения
- `cancelledSales[].unitName`: string — Название заведения
- `cancelledSales[].productId`: string — Идентификатор продукта
- `cancelledSales[].productName`: string — Наименование продукта
- `cancelledSales[].productPrice`: number — Цена без скидки в формате #.##
- `cancelledSales[].productPriceWithDiscount`: number — Цена со скидкой в формате #.##
- `cancelledSales[].taxRate`: number — Налоговая ставка в формате #.##
- `cancelledSales[].taxValue`: number — Сумма налога в формате #.##
- `cancelledSales[].shiftId`: string — Идентификатор смены
- `cancelledSales[].shiftStartedAtLocal`: string — Дата и время начала смены в формате ISO 8601 (локальное время)
- `cancelledSales[].shiftEndedAtLocal`: string — Дата и время окончания смены в формате ISO 8601 (локальное время)
- `cancelledSales[].addedIngredients`: array — Список добавленных ингридиентов
- `cancelledSales[].addedIngredients[].id`: string — Идентификатор добавленного ингредиента
- `cancelledSales[].addedIngredients[].name`: string — Название добавленного ингредиента
- `cancelledSales[].addedIngredients[].price`: number — Цена добавленного ингредиента в формате #.##
- `cancelledSales[].pizzaHalves`: array — Список выбранных пицц-половинок
- `cancelledSales[].pizzaHalves[].id`: string — Идентификатор пиццы половинки
- `cancelledSales[].pizzaHalves[].name`: string — Название пиццы половинки
- `cancelledSales[].pizzaHalves[].price`: number — Цена пиццы половинки в формате #.##
- `cancelledSales[].price`: number — Следует использовать productPriceWithDiscount. Цена со скидкой в формате #.##
- `cancelledSales[].soldAt`: string — Дата и время заказа (Возвращает локальное время вместо UTC)
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Продажи
**`GET /accounting/sales`**
> Возвращает продажи за указанный период (включительно), отсортированные по дате и идентификатору продажи. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `salesChannel` (string) — Канал продажи
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `orderSource` (string) — Источник заказа

Response (application/json):
- `sales`: array
- `sales[].orderId`: string — Идентификатор заказа
- `sales[].soldAtLocal`: string — Дата и время заказа в формате ISO 8601 (локальное время)
- `sales[].unitId`: string — Идентификатор заведения (пиццерии)
- `sales[].unitName`: string — Название заведения (пиццерии)
- `sales[].shiftId`: string
- `sales[].shiftStartedAtLocal`: string — Дата и время начала смены в формате ISO 8601 (локальное время)
- `sales[].shiftEndedAtLocal`: string — Дата и время окончания смены в формате ISO 8601 (локальное время)
- `sales[].cashBoxId`: string — Номер кассы DodoIS
- `sales[].cashBoxType`: string — Вид деятельности кассы
- `sales[].cashBoxNumber`: integer — Номер кассы
- `sales[].cashBoxSessionId`: string — Номер кассовой сессии
- `sales[].cashBoxSessionStartedAtLocal`: string — Дата и время начала смены в формате ISO 8601 (локальное время)
- `sales[].cashBoxSessionEndedAtLocal`: string — Дата и время окончания смены в формате ISO 8601 (локальное время)
- `sales[].paymentMethod`: string — Метод оплаты
- `sales[].paymentProviderName`: string — Название провайдера, который провёл оплату
- `sales[].paymentId`: string — Идентификатор платежа в DodoIS
- `sales[].paymentTransactionId`: string — Идентификатор транзакции в системе эквайринга
- `sales[].checkNumber`: integer — Номер чека, который выдал ККМ, показывает был ли распечатан чек для заказа
- `sales[].salesChannel`: string — Канал продаж заказа
- `sales[].orderSource`: string — Источник заказа
- `sales[].aggregatorName`: string — Название агрегатора
- `sales[].products`: array
- `sales[].products[].productId`: string — Идентификатор продукта
- `sales[].products[].isProducible`: boolean — Производимый (false - товар, true - продукт, который производим сами)
- `sales[].products[].defaultProductName`: string — Название, построенное с учётом признаков продукта
- `sales[].products[].price`: number — Цена без скидки в формате #.##
- `sales[].products[].priceWithDiscount`: number — Цена со скидкой в формате #.##
- `sales[].products[].taxRate`: number — Налоговая ставка в формате #.##
- `sales[].products[].taxValue`: number — Сумма налога в формате #.##
- `sales[].products[].combo`: object
- `sales[].products[].combo.id`: string — Идентификатор комбо
- `sales[].products[].combo.name`: string — Название комбо
- `sales[].products[].discount`: object
- `sales[].products[].discount.bonusActionId`: string — Идентификатор бонусной акции
- `sales[].products[].discount.bonusActionName`: string — Название бонусной акции
- `sales[].products[].discount.promoCode`: string — Промокод
- `sales[].products[].addedIngredients`: array — Список добавленных ингредиентов
- `sales[].products[].addedIngredients[].id`: string — Идентификатор добавленного ингредиента
- `sales[].products[].addedIngredients[].name`: string — Название добавленного ингредиента
- `sales[].products[].addedIngredients[].price`: number — Цена добавленного ингредиента в формате #.##
- `sales[].products[].pizzaHalves`: array — Список выбранных пицц-половинок
- `sales[].products[].pizzaHalves[].id`: string — Идентификатор пиццы половинки
- `sales[].products[].pizzaHalves[].name`: string — Название пиццы половинки
- `sales[].products[].pizzaHalves[].price`: number — Цена пиццы половинки в формате #.##
- `sales[].soldAt`: string — Дата и время продажи в формате ISO 8601 (Возвращает локальное время вместо UTC)
- `sales[].shiftStartedAt`: string — Дата и время начала смены в формате ISO 8601 (Возвращает локальное время вместо UTC)
- `sales[].shiftEndedAt`: string — Дата и время окончания смены в формате ISO 8601 (Возвращает локальное время вместо UTC)
- `sales[].cashBoxSessionStartedAt`: string — Дата и время начала смены в формате ISO 8601 (Возвращает локальное время вместо UTC)
- `sales[].cashBoxSessionEndedAt`: string — Дата и время окончания смены в формате ISO 8601 (Возвращает локальное время вместо UTC)
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Расход сырья за период
**`GET /accounting/stock-consumptions-by-period`**
> Возвращает расход сырья за указанный период (включительно), сгруппированный по идентификатору заведения, идентификатору сырья, типу расхода и единице измерения и отсортированный по идентификатору заведения, типу расхода и идентификатору сырья. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут ко

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `consumptions`: array — Список записей о расходах сырья
- `consumptions[].unitId`: string — Идентификатор заведения
- `consumptions[].unitName`: string — Наименование заведения
- `consumptions[].consumptionType`: string — Тип расхода
- `consumptions[].stockItemId`: string — Идентификатор сырья
- `consumptions[].stockItemName`: string — Наименование сырья
- `consumptions[].measurementUnit`: string — Наименование единицы измерения
- `consumptions[].quantity`: number — Количество расхода в формате #.###
- `consumptions[].costWithVat`: number — Сумма расхода с НДС. Цена актуальна на конец периода.
- `consumptions[].costWithoutVat`: number — Сумма расхода без НДС. Цена актуальна на конец периода.
- `consumptions[].currency`: string — Валюта
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Перемещения сырья
**`GET /accounting/stock-transfers`**
> Возвращает список перемещений сырья за указанный период (включительно), отсортированный по дате создания перемещения. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в 

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `statuses` (string) — Статусы перемещений
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `shippedFrom` (string) — Фильтр по дате отгрузки, начало диапазона в формате ISO 8601
- `shippedTo` (string) — Фильтр по дате отгрузки, конец диапазона в формате ISO 8601
- `receivedFrom` (string) — Фильтр по дате получения, начало диапазона в формате ISO 8601
- `receivedTo` (string) — Фильтр по дате получения, конец диапазона в формате ISO 8601
- `createdFrom` (string) — Фильтр по дате создания, начало диапазона в формате ISO 8601
- `createdTo` (string) — Фильтр по дате создания, конец диапазона в формате ISO 8601

Response (application/json):
- `transfers`: array — Список записей о расходах сырья
- `transfers[].transferOrderNumber`: string — Номер заявки
- `transfers[].transferOrderId`: string — Идентификатор заявки
- `transfers[].originUnitId`: string — Идентификатор заведения отгрузки
- `transfers[].originUnitName`: string — Название заведения отгрузки
- `transfers[].originLegalEntityId`: string — Идентификатор юрлица отгрузки
- `transfers[].originLegalEntityName`: string — Название юрлица отгрузки
- `transfers[].destinationUnitId`: string — Идентификатор заведения получателя
- `transfers[].destinationUnitName`: string — Название заведения получателя
- `transfers[].destinationLegalEntityId`: string — Идентификатор юрлица получателя
- `transfers[].destinationLegalEntityName`: string — Название юрлица получателя
- `transfers[].status`: string — Статус перемещения
- `transfers[].createdAtLocal`: string — Дата создания (локальное время)
- `transfers[].expectedAtLocal`: string — Ожидаемая дата получения (локальное время)
- `transfers[].shippedAtLocal`: string — Дата отгрузки (локальное время)
- `transfers[].receivedAtLocal`: string — Дата получения (локальное время)
- `transfers[].stockItemId`: string — Идентификатор сырья
- `transfers[].stockItemName`: string — Наименование отгруженного сырья
- `transfers[].orderedQuantity`: number — Заказанное количество
- `transfers[].shippedQuantity`: number — Отгруженное количество
- `transfers[].receivedQuantity`: number — Полученное количество
- `transfers[].measurementUnit`: string — Наименование единицы измерения
- `transfers[].pricePerUnitWithVat`: number — Цена за единицу измерения с НДС
- `transfers[].pricePerUnitWithoutVat`: number — Цена за единицу измерения без НДС
- `transfers[].sumPriceWithVat`: number — Сумма с НДС
- `transfers[].sumPriceWithoutVat`: number — Сумма без НДС
- `transfers[].taxRate`: number — Ставка НДС
- `transfers[].vatValue`: number — Сумма НДС
- `transfers[].createdAt`: string — Дата создания (Возвращает локальное время вместо UTC)
- `transfers[].expectedAt`: string — Ожидаемая дата получения (Возвращает локальное время вместо UTC)
- `transfers[].shippedAt`: string — Дата отгрузки (Возвращает локальное время вместо UTC)
- `transfers[].receivedAt`: string — Дата получения (Возвращает локальное время вместо UTC)
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Приходы сырья
**`GET /accounting/incoming-stock-items`**
> ⚠️ **DEPRECATED** - Пожалуйста, используйте [новый Accounting API](https://docs.dodois.io/docs/accounting/62c1e14c070bb-postavki-prihody-syrya) Возвращает список приходов за указанный период (включительно), отсортированный по дате получения и идентификатору записи. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока

Query:
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID

Response (application/json):
- `incomingStockItems`: array — Список записей о приходах
- `incomingStockItems[].unitId`: string — Идентификатор заведения
- `incomingStockItems[].unitName`: string — Название заведения
- `incomingStockItems[].stockItemId`: string — Идентификатор сырья
- `incomingStockItems[].stockItemName`: string — Название сырья
- `incomingStockItems[].quantity`: number — Полученное количество в формате #.###
- `incomingStockItems[].measurementUnit`: string — Наименование единицы измерения
- `incomingStockItems[].totalPriceWithoutVat`: number — Цена без налога в формате #.##
- `incomingStockItems[].totalPriceWithVat`: number — Цена с НДС в формате #.##
- `incomingStockItems[].pricePerMeasurementUnitWithVat`: number — Цена за единицу измерения с учетом НДС в формате #.##
- `incomingStockItems[].vatRate`: number — Ставка налога в %
- `incomingStockItems[].vatValue`: number — Размер налога
- `incomingStockItems[].incomingStockOrderItemCreatedAt`: string — Дата и время создания элемента поставки в UTC
- `incomingStockItems[].incomingStockOrderItemModifiedAt`: string — Дата и время последнего изменения элемента поставки в UTC
- `incomingStockItems[].incomingStockOrderId`: string — Идентификатор заявки
- `incomingStockItems[].suppliedAtLocal`: string — Дата и время поставки (локальное время)
- `incomingStockItems[].supplierId`: string — Идентификатор поставщика
- `incomingStockItems[].invoiceDate`: string — Дата накладной
- `incomingStockItems[].invoiceNumber`: string — Номер накладной
- `incomingStockItems[].commercialInvoiceNumber`: string — Номер счет фактуры
- `incomingStockItems[].incomingStockOrderCreatedAt`: string — Дата и время создания заявки на поставку в UTC
- `incomingStockItems[].incomingStockOrderModifiedAt`: string — Дата и время последнего изменения заявки на поставку в UTC
- `incomingStockItems[].suppliedAt`: string — Дата и время поставки (Возвращает локальное время вместо UTC)
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Складские остатки
**`GET /accounting/inventory-stocks`**
> Возвращает складские остатки. Если сырьё не участвовало в ревизиях 60 дней, оно больше не возвращается в ответе этого метода. ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 3. В `stockItems` следует перечислять UUID-ы строго через запятую без пробелов; > #### Доступно для 

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `stockItems` (string) — Список сырья в формате UUID
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `categories` (string) — Категории сырья

Body: 

Response (application/json):
- `stocks`: array
- `stocks[].id`: string — Идентификатор материала/продукта
- `stocks[].name`: string — Название материала/продукта
- `stocks[].unitId`: string — Идентификатор заведения
- `stocks[].categoryName`: string — Категория сырья
- `stocks[].quantity`: number — Количество
- `stocks[].measurementUnit`: string — Наименование единицы измерения
- `stocks[].balanceInMoney`: number — Остаток в деньгах
- `stocks[].currency`: string — Валюта
- `stocks[].avgWeekdayExpense`: number — Средний расход в будни
- `stocks[].avgWeekendExpense`: number — Средний расход в выходные
- `stocks[].daysUntilBalanceRunsOut`: integer — На сколько дней хватит запаса
- `stocks[].calculatedAt`: string — Дата и время на которые актуальны данные об скаладских остатках
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Учёт → Расход теста
**`GET /accounting/dough-consumption`**
> Позволяет получить расход теста по часам. В ответе будут включены только те промежутки, в которых происходил расход теста. Так например если заведение работает с 09:00 - 23:00, то в ответе будут только промежутки между 09:00 - 23:00. Любой отсутствующий промежуток можно считать за 0. ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` сле

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `fromDate` (string, required) — Дата начала периода в формате ISO 8601
- `toDate` (string, required) — Дата конца периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `consumption`: array
- `consumption[].unitdId`: string — Идентификатор заведения
- `consumption[].fromLocal`: string — Начало периода
- `consumption[].toLocal`: string — Окончание периода
- `consumption[].doughSize`: integer — Размер теста
- `consumption[].quantity`: number — Количество теста
- `consumption[].measurementUnit`: string — Наименование единицы измерения
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

## Финансы

### Финансы → Дневные продажи по стране
**`GET /finances/sales/country/daily`**
> Возвращает дневные данные продаж по стране за указанный период. Данные продаж агрегированы по дням и разбиты по каналам продаж, источникам заказа и методам оплаты. В данных продаж учитываются и возвращаются только заказы с перечисленными условиями: - Допустимые каналы продаж: Delivery, Dine-in, Takeaway - Допустимые источники заказов: CallCenter, Website, Dine-in, MobileApp, Manager, Aggregator, K

Query:
- `fromDate` (string, required) — Дата начала периода в формате ISO 8601
- `toDate` (string, required) — Дата конца периода в формате ISO 8601

Response (application/json):
- `result`: array — Данные о продажах по дням
- `result[].date`: string — Дата в формате ISO 8601
- `result[].sales`: number — Объём продаж без учёта VAT
- `result[].ordersCount`: integer — Количество заказов
- `result[].salesBreakdown`: array — Продажи и количество заказов по каналам продаж, источникам заказа и типам платежей
- `result[].salesBreakdown[].orderSource`: string — Источник заказа
- `result[].salesBreakdown[].salesChannel`: string — Канал продаж заказа
- `result[].salesBreakdown[].paymentMethod`: string — Метод оплаты
- `result[].salesBreakdown[].sales`: number — Объём продаж без учёта VAT
- `result[].salesBreakdown[].ordersCount`: integer — Количество заказов

### Финансы → Дневные продажи по заведениям
**`GET /finances/sales/units/daily`**
> Возвращает дневные данные продаж по заведениям за указанный период. Данные продаж агрегированы по дням и разбиты по каналам продаж, источникам заказа и методам оплаты. В данных продаж учитываются и возвращаются только заказы с перечисленными условиями: - Допустимые каналы продаж: Delivery, Dine-in, Takeaway - Допустимые источники заказов: CallCenter, Website, Dine-in, MobileApp, Manager, Aggregato

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `fromDate` (string, required) — Дата начала периода в формате ISO 8601
- `toDate` (string, required) — Дата конца периода в формате ISO 8601

Response (application/json):
- `result`: array — Данные о продажах по дням и заведениям
- `result[].date`: string — Дата в формате ISO 8601
- `result[].unitId`: string — Идентификатор заведения
- `result[].sales`: number — Объём продаж без учёта VAT
- `result[].ordersCount`: integer — Количество заказов
- `result[].salesBreakdown`: array — Продажи и количество заказов по каналам продаж, источникам заказа и типам платежей
- `result[].salesBreakdown[].orderSource`: string — Источник заказа
- `result[].salesBreakdown[].salesChannel`: string — Канал продаж заказа
- `result[].salesBreakdown[].paymentMethod`: string — Метод оплаты
- `result[].salesBreakdown[].sales`: number — Объём продаж без учёта VAT
- `result[].salesBreakdown[].ordersCount`: integer — Количество заказов

### Финансы → Месячные продажи по стране
**`GET /finances/sales/country/monthly`**
> Возвращает месячные данные продаж по стране за указанный период. Данные продаж агрегированы по месяцам и разбиты по каналам продаж, источникам заказа и методам оплаты. В данных продаж учитываются и возвращаются только заказы с перечисленными условиями: - Допустимые каналы продаж: Delivery, Dine-in, Takeaway - Допустимые источники заказов: CallCenter, Website, Dine-in, MobileApp, Manager, Aggregato

Query:
- `fromDate` (string, required) — Дата начала периода в формате ISO 8601
- `toDate` (string, required) — Дата конца периода в формате ISO 8601

Response (application/json):
- `result`: array — Данные о продажах по месяцам
- `result[].year`: integer — Год
- `result[].month`: integer — Месяц
- `result[].sales`: number — Объём продаж без учёта VAT
- `result[].ordersCount`: integer — Количество заказов
- `result[].salesBreakdown`: array — Продажи и количество заказов по каналам продаж, источникам заказа и типам платежей
- `result[].salesBreakdown[].orderSource`: string — Источник заказа
- `result[].salesBreakdown[].salesChannel`: string — Канал продаж заказа
- `result[].salesBreakdown[].paymentMethod`: string — Метод оплаты
- `result[].salesBreakdown[].sales`: number — Объём продаж без учёта VAT
- `result[].salesBreakdown[].ordersCount`: integer — Количество заказов

### Финансы → Месячные продажи по заведениям
**`GET /finances/sales/units/monthly`**
> Возвращает месячные данные продаж по заведениям за указанный период. Данные продаж агрегированы по месяцам и разбиты по каналам продаж, источникам заказа и методам оплаты. В данных продаж учитываются и возвращаются только заказы с перечисленными условиями: - Допустимые каналы продаж: Delivery, Dine-in, Takeaway - Допустимые источники заказов: CallCenter, Website, Dine-in, MobileApp, Manager, Aggre

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `fromDate` (string, required) — Дата начала периода в формате ISO 8601
- `toDate` (string, required) — Дата конца периода в формате ISO 8601

Response (application/json):
- `result`: array — Данные о продажах по месяцам и заведениям
- `result[].year`: integer — Год
- `result[].month`: integer — Месяц
- `result[].unitId`: string — Идентификатор заведения
- `result[].sales`: number — Объём продаж без учёта VAT
- `result[].ordersCount`: integer — Количество заказов
- `result[].salesBreakdown`: array — Продажи и количество заказов по каналам продаж, источникам заказа и типам платежей
- `result[].salesBreakdown[].orderSource`: string — Источник заказа
- `result[].salesBreakdown[].salesChannel`: string — Канал продаж заказа
- `result[].salesBreakdown[].paymentMethod`: string — Метод оплаты
- `result[].salesBreakdown[].sales`: number — Объём продаж без учёта VAT
- `result[].salesBreakdown[].ordersCount`: integer — Количество заказов

### Финансы → Продажи по стране за период
**`GET /finances/sales/country`**
> Возвращает данные продаж по стране за указанный период. Данные продаж разбиты по каналам продаж, источникам заказа и методам оплаты. В данных продаж учитываются и возвращаются только заказы с перечисленными условиями: - Допустимые каналы продаж: Delivery, Dine-in, Takeaway - Допустимые источники заказов: CallCenter, Website, Dine-in, MobileApp, Manager, Aggregator, Kiosk, ChatBot - Допустимые виды

Query:
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601

Response (application/json):
- `result`: array — Данные о продажах за период
- `result[].sales`: number — Объём продаж без учёта VAT
- `result[].ordersCount`: integer — Количество заказов
- `result[].salesBreakdown`: array — Продажи и количество заказов по каналам продаж, источникам заказа и типам платежей
- `result[].salesBreakdown[].orderSource`: string — Источник заказа
- `result[].salesBreakdown[].salesChannel`: string — Канал продаж заказа
- `result[].salesBreakdown[].paymentMethod`: string — Метод оплаты
- `result[].salesBreakdown[].sales`: number — Объём продаж без учёта VAT
- `result[].salesBreakdown[].ordersCount`: integer — Количество заказов

### Финансы → Продажи по заведениям за период
**`GET /finances/sales/units`**
> Возвращает данные продаж по заведениям за указанный период. Данные продаж агрегированы по месяцам и разбиты по каналам продаж, источникам заказа и методам оплаты. В данных продаж учитываются и возвращаются только заказы с перечисленными условиями: - Допустимые каналы продаж: Delivery, Dine-in, Takeaway - Допустимые источники заказов: CallCenter, Website, Dine-in, MobileApp, Manager, Aggregator, Ki

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601

Response (application/json):
- `result`: array — Данные о продажах по заведениям за период
- `result[].unitId`: string — Идентификатор заведения
- `result[].sales`: number — Объём продаж без учёта VAT
- `result[].ordersCount`: integer — Количество заказов
- `result[].salesBreakdown`: array — Продажи и количество заказов по каналам продаж, источникам заказа и типам платежей
- `result[].salesBreakdown[].orderSource`: string — Источник заказа
- `result[].salesBreakdown[].salesChannel`: string — Канал продаж заказа
- `result[].salesBreakdown[].paymentMethod`: string — Метод оплаты
- `result[].salesBreakdown[].sales`: number — Объём продаж без учёта VAT
- `result[].salesBreakdown[].ordersCount`: integer — Количество заказов

## Заказы

### Заказы → Статистика по новым клиентам
**`GET /orders/clients-statistics`**
> Получение статистики по клиентам в заведении. Возвращает информацию о новых клиентах в заведении и на достаку. Так же информацию о старых клиентах. ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов; 3. `fromDate` должен быть меньше или равен `toDate`. 4. Максимальный период за

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `fromDate` (string, required) — Дата начала периода в формате ISO 8601
- `toDate` (string, required) — Дата конца периода в формате ISO 8601

Response (application/json):
- `newClientsCount`: integer — Количество новых клиентов
- `dineInNewClientsCount`: integer — Количество новых клиентов в заведении
- `deliveryAndTakeawayNewClientsCount`: integer — Количество новых клиентов на доставку/самовывоз
- `oldClientsCount`: integer — Количество старых клиентов

## Заведения

### Заведения → Смены заведений
**`GET /units/shifts`**
> Возвращает список смен заведений, отсортированный по дате начала смены. Смены выбираются по времени начала (eсли начало смены попало в диапазон from – to включительно). Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID
- `from` (string, required) — Начало периода в формате ISO 8601
- `to` (string, required) — Конец периода в формате ISO 8601
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать

Response (application/json):
- `shifts`: array — Смены заведений
- `shifts[].id`: string — Идентификатор смены
- `shifts[].unitId`: string — Идентификатор заведения
- `shifts[].unitName`: string — Название заведения
- `shifts[].previousShiftEndedAtLocal`: string — Дата и время окончания предыдущей смены в формате ISO 8601 (локальное время)
- `shifts[].startedAtLocal`: string — Дата и время начала смены в формате ISO 8601 (локальное время)
- `shifts[].endedAtLocal`: string — Дата и время окончания смены в формате ISO 8601 (локальное время)
- `shifts[].isOpen`: boolean — Индикатор того, что смена открыта
- `shifts[].openedByUserId`: string — Id пользователя, который открыл смену
- `shifts[].previousShiftEndedAt`: string — Дата и время окончания предыдущей смены в формате ISO 8601 (Возвращает локальное время вместо UTC)
- `shifts[].startedAt`: string — Дата и время начала смены в формате ISO 8601 (Возвращает локальное время вместо UTC)
- `shifts[].endedAt`: string — Дата и время окончания смены в формате ISO 8601 (Возвращает локальное время вместо UTC)
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Заведения → Информация о заведениях
**`GET /units`**
> Возвращает список заведений с основной информацией о них. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам: 1. В `countryId` нужно передавать идентификатор страны в формате ISO 3166-1 alpha-2; 2. В `businessId` нужно 

Query:
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `unitTypes` (string) — Список типов заведения
- `unitStates` (string) — Состояние юнита
- `countryId` (string, required) — Идентификатор страны в формате ISO 3166-1 alpha-2
- `businessId` (string, required) — Идентификатор бизнеса в формате UUID
- `units` (string) — Список заведений (пиццерий) Dodo IS в формате UUID
- `organizations` (string) — Организации Dodo IS в формате UUID

Response (application/json):
- `units`: array — Список заведений
- `units[].id`: string — Идентификатор юнита
- `units[].state`: string — Состояние юнита
- `units[].organizationId`: string — Идентификатор организации
- `units[].organizationName`: string — Название организации
- `units[].name`: string — Название юнита
- `units[].alias`: string — Дополнительное название юнита
- `units[].type`: string — Тип юнита
- `units[].address`: string — Адрес юнита
- `units[].countryId`: string — Идентификатор страны в формате ISO 3166-1 alpha-2
- `units[].businessId`: string — Идентификатор бизнеса
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Заведения → Информация о пиццериях/кофейнях
**`GET /units/stores`**
> Возвращает список пиццерий/кофеен с информацией о них. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам: 1. В `countryId` нужно передавать идентификатор страны в формате ISO 3166-1 alpha-2; 2. В `businessId` нужно пер

Query:
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `unitStates` (string) — Состояние юнита
- `countryId` (string, required) — Идентификатор страны в формате ISO 3166-1 alpha-2
- `businessId` (string, required) — Идентификатор бизнеса в формате UUID
- `organizations` (string) — Организации Dodo IS в формате UUID
- `units` (string) — Список заведений (пиццерий) Dodo IS в формате UUID

Response (application/json):
- `stores`: array — Список заведений
- `stores[].id`: string — Идентификатор заведения
- `stores[].name`: string — Название заведения
- `stores[].alias`: string — Дополнительное название заведения
- `stores[].aliasTransliteration`: string — Транслитерированное название заведения
- `stores[].isApproved`: boolean — Дано разрешение на открытие заведения
- `stores[].state`: string — Состояние заведения
- `stores[].organizationId`: string — Идентификатор организации
- `stores[].organizationName`: string — Название организации
- `stores[].firstOperatingDay`: string — Первый день работы заведения. Или null, если заведение ещё не открыто
- `stores[].location`: object — Информация о местоположении заведения
- `stores[].location.latitude`: number — Широта
- `stores[].location.longitude`: number — Долгота
- `stores[].location.сountry`: string — Страна
- `stores[].location.region`: string — Административная единица
- `stores[].location.district`: string — Округ
- `stores[].location.locality`: string — Населённый пункт
- `stores[].location.street`: string — Улица
- `stores[].location.house`: string — Номер дома
- `stores[].location.postCode`: string — Почтовый индекс
- `stores[].location.comment`: string — Комментарий к адресу
- `stores[].location.landmark`: string — Оринтиентир для поиска здания
- `stores[].location.metroStations`: array — Перечень станций метро, связанных с заведением. Если метро не задано, то будет возвращён пустой массив
- `stores[].location.fullAddress`: string — Полный адрес заведения
- `stores[].orderTypes`: array — Типы заказов в заведении. Может быть пустым массивом
- `stores[].salesChannels`: array — Каналы продажи. Может быть пустым массивом
- `stores[].dateTimeInfo`: object — Информация о часовом поясе и текущем времени
- `stores[].dateTimeInfo.currentDateTime`: string — Локальное время юнита
- `stores[].dateTimeInfo.timeZoneShift`: number — Часовой пояс, в котором находится юнит
- `stores[].dateTimeInfo.timeZone`: string — Временная зона, GMT+X, где X — это количество часов и минут смещения от Гринвичского времени.
- `stores[].workingSchedule`: object — График работы заведения
- `stores[].workingSchedule.delivery`: array — Время работы доставки
- `stores[].workingSchedule.delivery[].dayOfWeek`: string — День недели
- `stores[].workingSchedule.delivery[].beginTime`: string — Время начала работы. Значение null проставляется, если юнит работает круглосуточно или день отмечен как выходной
- `stores[].workingSchedule.delivery[].endTime`: string — Время окончания работы. Значение null проставляется, если юнит работает круглосуточно или день отмечен как выходной
- `stores[].workingSchedule.delivery[].isRoundTheClock`: boolean — Работает ли юнит в этот день круглосуточно
- `stores[].workingSchedule.delivery[].isClosed`: boolean — Является ли день выходным
- `stores[].workingSchedule.stationary`: array — Время работы заведения
- `stores[].workingSchedule.stationary[].dayOfWeek`: string — День недели
- `stores[].workingSchedule.stationary[].beginTime`: string — Время начала работы. Значение null проставляется, если юнит работает круглосуточно или день отмечен как выходной
- `stores[].workingSchedule.stationary[].endTime`: string — Время окончания работы. Значение null проставляется, если юнит работает круглосуточно или день отмечен как выходной
- `stores[].workingSchedule.stationary[].isRoundTheClock`: boolean — Работает ли юнит в этот день круглосуточно
- `stores[].workingSchedule.stationary[].isClosed`: boolean — Является ли день выходным
- `stores[].premiseParameters`: object — Параметры заведения
- `stores[].premiseParameters.storeFormat`: string — Формат заведения
- `stores[].premiseParameters.square`: number — Площадь заведения в метрах квадратных
- `stores[].paymentInfo`: object — Информация о способах оплаты
- `stores[].paymentInfo.courierTerminal`: boolean — Терминал у курьера
- `stores[].paymentInfo.pickupCardPay`: boolean — Доступна ли оплата по карте при самовывозе
- `stores[].storeFeatures`: object — Дополнительные услуги заведения
- `stores[].storeFeatures.kidsRoom`: boolean — Есть детская комната
- `stores[].storeFeatures.tableDelivery`: boolean — Доставка до столика
- `stores[].directors`: array — Список руководителей заведения
- `stores[].directors[].userId`: string — Идентификатор пользователя
- `stores[].directors[].directorType`: string — Тип руководителя
- `stores[].countryId`: string — Идентификатор страны в формате ISO 3166-1 alpha-2
- `stores[].businessId`: string — Идентификатор бизнеса
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Заведения → Информация о ПРЦ
**`GET /units/distributioncenters`**
> Возвращает список ПРЦ (производственно-распределительный центр) с информацией о них. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). ### Требования к query параметрам: 1. В `countryId` нужно передавать идентификатор страны в формате ISO 3166-1 alpha-

Query:
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `countryId` (string, required) — Идентификатор страны в формате ISO 3166-1 alpha-2
- `businessId` (string, required) — Идентификатор бизнеса в формате UUID
- `unitStates` (string) — Состояние юнита
- `units` (string) — Список ПРЦ Dodo IS в формате UUID
- `organizations` (string) — Организации Dodo IS в формате UUID

Response (application/json):
- `distributionCenters`: array — Список ПРЦ
- `distributionCenters[].id`: string — Идентификатор ПРЦ
- `distributionCenters[].name`: string — Название
- `distributionCenters[].alias`: string — Псевдоним
- `distributionCenters[].state`: string — Состояние ПРЦ
- `distributionCenters[].organizationId`: string — Идентификатор организации
- `distributionCenters[].organizationName`: string — Название организации
- `distributionCenters[].location`: object — Информация о местоположении заведения
- `distributionCenters[].location.latitude`: number — Широта
- `distributionCenters[].location.longitude`: number — Долгота
- `distributionCenters[].location.сountry`: string — Страна
- `distributionCenters[].location.region`: string — Административная единица
- `distributionCenters[].location.district`: string — Округ
- `distributionCenters[].location.locality`: string — Населённый пункт
- `distributionCenters[].location.street`: string — Улица
- `distributionCenters[].location.house`: string — Номер дома
- `distributionCenters[].location.postCode`: string — Почтовый индекс
- `distributionCenters[].location.comment`: string — Комментарий к адресу
- `distributionCenters[].location.landmark`: string — Оринтиентир для поиска здания
- `distributionCenters[].location.metroStations`: array — Перечень станций метро, связанных с заведением. Если метро не задано, то будет возвращён пустой массив
- `distributionCenters[].location.fullAddress`: string — Полный адрес заведения
- `distributionCenters[].dateTimeInfo`: object — Информация о часовом поясе и текущем времени
- `distributionCenters[].dateTimeInfo.currentDateTime`: string — Локальное время юнита
- `distributionCenters[].dateTimeInfo.timeZoneShift`: number — Часовой пояс, в котором находится юнит
- `distributionCenters[].dateTimeInfo.timeZone`: string — Временная зона, GMT+X, где X — это количество часов и минут смещения от Гринвичского времени.
- `distributionCenters[].workingSchedule`: array — График работы ПРЦ
- `distributionCenters[].workingSchedule[].dayOfWeek`: string — День недели
- `distributionCenters[].workingSchedule[].beginTime`: string — Время начала работы. Значение null проставляется, если юнит работает круглосуточно или день отмечен как выходной
- `distributionCenters[].workingSchedule[].endTime`: string — Время окончания работы. Значение null проставляется, если юнит работает круглосуточно или день отмечен как выходной
- `distributionCenters[].workingSchedule[].isRoundTheClock`: boolean — Работает ли юнит в этот день круглосуточно
- `distributionCenters[].workingSchedule[].isClosed`: boolean — Является ли день выходным
- `distributionCenters[].isManufacture`: boolean — Является ли мануфактурой
- `distributionCenters[].countryId`: string — Идентификатор страны в формате ISO 3166-1 alpha-2
- `distributionCenters[].businessId`: string — Идентификатор бизнеса
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Заведения → Производственные станции
**`GET /units/work-stations`**
> Возвращает список производственных подстанций. ### Требования к query параметрам: 1. В `units` можно перечислить до 30 заведений в одном запросе; 2. В `units` следует перечислять UUID-ы строго через запятую без пробелов;

Query:
- `units` (string, required) — Список заведений (пиццерий) Dodo IS в формате UUID

Response (application/json):
- `workStationsByUnit`: array
- `workStationsByUnit[].unitId`: string — Идентификатор заведения
- `workStationsByUnit[].workStations`: array
- `workStationsByUnit[].workStations[].id`: string — Идентификатор производственной станции
- `workStationsByUnit[].workStations[].name`: string — Производственная станция (Доставка, Кухня, Касса)
- `workStationsByUnit[].workStations[].subStationName`: string — Производственная подстанция (Холодный цех, Чистота и тд)
- `workStationsByUnit[].workStations[].availableStaffPositions`: array — Должности сотрудников которые могут работать на данной производственной станции

### Заведения → Цели на месяц (GET)
**`GET /units/month-goals`**
> Возвращает цели пиццерии на месяц. > #### Доступно для следующих ролей: > > `Division administrator` - Администратор подразделения > > `Store Manager` - Менеджер офиса > > `Shift supervisor` - Менеджер смены

Query:
- `unit` (string, required) — Заведение (пиццерия) Dodo IS в формате UUID
- `year` (integer, required) — Год
- `month` (integer, required) — месяц

Body: 

Response (application/json):
- `sales`: number — Выручка общая
- `deliverySales`: number — Выручка доставки
- `salesPerPerson`: number — Выручка на человека в час (₽/чел. в час)
- `productsPerPerson`: number — Количество пицц и закусок на человека в час (шт/чел. в час)
- `salesPerCourier`: number — Выручка на курьера в час (₽/кур. в час)
- `ordersPerCourier`: number — Количество заказов на курьера в час (шт/кур. в час)
- `certificates`: integer — Количество сертификатов за опоздание (шт.)
- `leakage`: number — Неучтенные потери (%)
- `writeOffsDueToDefectiveProduct`: number — Списания из-за брака (%)
- `defectiveProduct`: number — Брак (%)

### Заведения → Цели на месяц (PATCH)
**`PATCH /units/month-goals`**
> Изменение цель пиццерии на месяц. ### Требования к query параметрам: 1. Нельзя изменить цель пиццерии в прошлом. 2. Если не передать какой-либо из параметров или передать null, то изменение для данного парметра не применится. Позволяет точечно изменять только 1 из параметров. > #### Доступно для следующих ролей: > > `Division administrator` - Администратор подразделения > > `Store Manager` - Менед

Body: application/json

## Оргструктура

### Оргструктура → Список юрлиц
**`GET /organization-structure/legal-entities`**
> Возвращает список юрлиц, отсортированный по ID. Для получения данных необходимо указывать параметр `skip`, смещая его на количество уже полученных записей. Повторять до тех пор, пока не будет достигнут конец списка (`isEndOfListReached = true`). <b>ВНИМАНИЕ:</b> параметры `takenCount` и `totalCount` скоро будут удалены!

Query:
- `typeIds` (array) — Список id типов юрлиц через запятую
- `skip` (integer) — Количество записей, которые следует пропустить
- `take` (integer) — Количество записей, которые следует выбрать
- `modifiedAt` (string) — Дата и время изменения в формате ISO 8601. Фильтр возвращает записи у которых дата изменения больше или равна переданной

Response (application/json):
- `legalEntities`: array
- `legalEntities[].id`: string — Идентификатор юрлица
- `legalEntities[].name`: string — Название
- `legalEntities[].typeId`: string — Идентификатор типа юрлица
- `legalEntities[].typeName`: string — Название типа юрлица (сокращённое)
- `legalEntities[].requisites`: array — Реквизиты
- `legalEntities[].requisites[].name`: string — Название
- `legalEntities[].requisites[].value`: string — Значение
- `legalEntities[].countryCode`: integer — Код страны
- `legalEntities[].address`: string — Адрес
- `legalEntities[].modifiedAt`: string — Дата и время изменения в формате ISO 8601
- `takenCount`: integer — Получено записей
- `totalCount`: integer — Всего записей
- `isEndOfListReached`: boolean — Индикатор того, что достигнут конец списка. Выдаёт true, если достигнут конец списка

### Оргструктура → Список типов юрлиц
**`GET /organization-structure/legal-entity-types`**
> Возвращает список типов юрлиц (ООО, ИП, и т.д.), отсортированный по ID.

Response (application/json):
- `legalEntityTypes`: array
- `legalEntityTypes[].id`: string — Идентификатор типа юрлица
- `legalEntityTypes[].fullName`: string — Полное название
- `legalEntityTypes[].shortName`: string — Сокращенное название
- `legalEntityTypes[].countryCode`: integer — Код страны

### Оргструктура → Список населённых пунктов
**`GET /organization-structure/localities`**
> Возвращает список публичных населённых пунктов, в которых есть подразделения со статусом «Открыто» или «Скоро открытие», отсортированный по наименованию населённого пункта. ### Требования к query параметрам: 1. Параметр `countryId` является обязательным; 2. В `countryId` нужно передавать идентификатор страны в формате ISO 3166-1 alpha-2. > **Доступно** по запросу в `support`. Данный запрос может б

Query:
- `countryId` (string, required) — Идентификатор страны в формате ISO 3166-1 alpha-2

Response (application/json):
- `localities`: array — Список населённых пунктов
- `localities[].id`: string — Идентификатор населённого пункта
- `localities[].name`: string — Название населённого пункта
- `localities[].transliteration`: string — Транслитерация названия населённого пункта на английский язык
- `localities[].countryId`: string — Идентификатор страны в формате ISO 3166-1 alpha-2

