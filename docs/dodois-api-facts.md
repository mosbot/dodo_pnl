# Dodo IS API — факты из официальных спек

Сгенерировано `_scripts/gen_dodois_facts.py` из `docs/dodois-openapi/*.yaml`
(OpenAPI от Dodo Brands). **Руками не править — перегенерировать.**

Здесь только то, что дословно записано в спеках. Ничего не выведено и не
дополнено по памяти. Если утверждение в любой нашей прозе расходится с этим
файлом — правы спеки или живой вызов, не проза.

Чего в спеках НЕТ и что отсюда узнать нельзя: какие поля реально приходят
сверх схемы, какие скоупы ручка потребует на самом деле (в спеке заявлен
минимум — см. инцидент со `shared` в `dodois-api.md`), и работает ли ручка
в конкретной стране.

## 1. Страны и хосты

Полный список серверов из `dodo-is-api-v2.yaml` — единственное место,
где Dodo перечисляет страны поимённо. `.io` и `.com` — разные кластеры;
хост определяется страной, а не нашим удобством.

| Хост и бренд | Страны |
|---|---|
| `api.dodois.com · dodopizza` | ae (ОАЭ), am (Армения), az (Азербайджан), bg (Болгария), cy (Кипр), de (Германия), ee (Эстония), es (Испания), gb (Великобритания), ge (Грузия), hr (Хорватия), hu (Венгрия), id (Индонезия), iq (Ирак), lt (Литва), ma (Марокко), md (Молдова), me (Черногория), mn (Монголия), mx (Мексика), ng (Нигерия), pl (Польша), qa (Катар), ro (Румыния), rs (Сербия), si (Словения), tr (Турция), vn (Вьетнам) |
| `api.dodois.com · drinkit` | ae (ОАЭ), az (Азербайджан), us (США) |
| `api.dodois.io · dodopizza` | by (Беларусь), kg (Киргизия), kz (Казахстан), ru (Россия), tj (Таджикистан), uz (Узбекистан) |
| `api.dodois.io · drinkit` | kz (Казахстан), ru (Россия) |

Кластер `api.dodois.io` для Dodo Pizza — ровно 6 стран: by, kg, kz, ru, tj, uz. Все остальные — на `api.dodois.com`.

Полный набор стран Dodo Pizza (34) — любая наша таблица стран (numeric→alpha-2, валюта, часовой пояс) должна покрывать его целиком, иначе непокрытая страна молча схлопнется в дефолт:

```
ae am az bg by cy de ee es gb ge hr hu id iq kg kz lt ma md me mn mx ng pl qa ro rs ru si tj tr uz vn
```

## 2. Базовый URL по разделам

Ключевая ловушка: сегмент страны есть НЕ во всех разделах. Код, который
клеит `/{business}/{country}` ко всему подряд, ломается ровно здесь.

| Файл спеки | API | Базовые URL | Страна в пути |
|---|---|---|---|
| `accounting-api.yaml` | Accounting API | `https://api.dodois.com/accounting`<br>`https://api.dodois.io/accounting` | нет |
| `auth-api.yaml` | Auth API | `https://api.dodois.com/auth`<br>`https://api.dodois.io/auth` | нет |
| `controlling-api.yaml` | Customer Rating API | `https://api.dodois.com/controlling`<br>`https://api.dodois.io/controlling` | нет |
| `dodo-is-api-v2.yaml` | Dodo IS API | `https://api.dodois.com/{brand}/{country}`<br>`https://api.dodois.io/{brand}/{country}` | страна И бренд |
| `franchisee-api.yaml` | Сети заведений | `https://api.dodois.com/franchisee`<br>`https://api.dodois.io/franchisee` | нет |
| `inventory-api.yaml` | Inventory API | `https://api.dodois.com/dodopizza/inventory`<br>`https://api.dodois.io/dodopizza/inventory`<br>`https://api.dodois.io/drinkit/inventory` | только бренд |
| `iot-api.yaml` | IoT API | `https://api.dodois.com/iot`<br>`https://api.dodois.io/iot` | нет |
| `label-printer-api.yaml` | Label printer API | `https://api.dodois.com/label-printer/dodopizza`<br>`https://api.dodois.io/label-printer/dodopizza`<br>`https://api.dodois.io/label-printer/drinkit` | только бренд |
| `marketplace-api.yaml` | Магазин приложений | `https://api.dodois.com/marketplace`<br>`https://api.dodois.io/marketplace` | нет |
| `pospayments.yaml` | Pos Payments API | `https://api.dodois.com/pospayments`<br>`https://api.dodois.io/pospayments` | нет |
| `ratings-api.yaml` | Customer Rating API | `https://api.dodois.com/customer-feedback`<br>`https://api.dodois.com/dodopizza/customer-feedback`<br>`https://api.dodois.com/drinkit/customer-feedback`<br>`… ещё 3` | только бренд |
| `staff-api.yaml` | Staff API | `https://api.dodois.io/staff` | нет |

## 3. Эндпоинты

### Accounting API (`accounting-api.yaml`)

- **`GET /stock-transfer/transfer-items`** — Перемещения → Позиции перемещений
  - scopes: `accounting:read`
  - границы из спеки: `units` maxItems=30; `statuses` enum=Created|Ordered|Shipped|Received|Cancelled; `skip` min=0, default=0; `take` max=1000, min=1, default=100
- **`POST /stock-transfer/transfers`** — Перемещения → Создание перемещения
  - scopes: `accounting:write`
- **`GET /write-offs-by-order`** — Инвентаризация → Списания сырья на производство
  - scopes: `accounting`, `accounting:read`
  - границы из спеки: `units` maxLen=329; `skip` min=0, default=0; `take` max=1000, min=0, maxLen=1000, default=100
- **`GET /write-offs/stock-items`** — Учёт → Списанное сырье
  - scopes: `accounting:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=1, default=100
- **`GET /write-offs/products`** — Учёт → Списанные продукты
  - scopes: `accounting:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=1, default=100
- **`GET /stock-transfer/returns`** — Перемещения → Возврат сырья
  - scopes: `accounting:read`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=1, default=100; `units` maxLen=329; `states` enum=Created|InProgress|Processed
- **`GET /incoming-stock/supplies`** — Поставки → Приходы сырья
  - scopes: `accounting:read`
  - границы из спеки: `skip` min=0, default=0; `take` max=100, min=1, default=10; `units` maxItems=30; `includeRemoved` default=False
- **`POST /incoming-stock/supplies`** — Поставки → Поступление сырья
  - scopes: `accounting:write`
- **`GET /documents/{documentId}/changes`** — Документы → История изменений
  - scopes: `accounting:read`
- **`GET /catalogs/stock-items`** — Справочники → Сырьё
  - scopes: `accounting:read`
  - границы из спеки: `units` maxLen=989; `includeRemoved` default=False; `includeArchived` default=False; `skip` min=0, default=0; `take` max=1000, min=1, default=100
- **`GET /manufactures/catalogs/stock-items/compositions`** — Справочники → Составы сырья
  - scopes: `accounting:manufacture`
  - границы из спеки: `take` max=1000, min=1, default=100
- **`GET /manufactures/catalogs/stock-items/{id}/composition`** — Справочники → Состав сырья
  - scopes: `accounting:manufacture`
- **`GET /manufactures/catalogs/shelf-life-settings`** — Справочники → Настройки условий хранения
  - scopes: `accounting:manufacture`
  - границы из спеки: `take` max=1000, min=1, default=100; `includeRemoved` default=False
- **`GET /manufactures/catalogs/shelf-life-settings/{id}`** — Справочники → Информация о настройках условий хранения
  - scopes: `accounting:manufacture`
- **`GET /manufactures/{manufactureId}/containers`** — Мануфактуры → Типы тар
  - scopes: `accounting:manufacture`
  - границы из спеки: `take` max=1000, min=1, default=100; `includeRemoved` default=False
- **`GET /manufactures/{manufactureId}/stock-items/containers/bindings`** — Мануфактуры → Привязки типов тар к сырью
  - scopes: `accounting:manufacture`
  - границы из спеки: `take` max=1000, min=1, default=100; `includeRemoved` default=False
- **`GET /manufactures/{manufactureId}/stock-items/available`** — Мануфактуры → Доступное к заказу сырьё
  - scopes: `accounting:manufacture`
  - границы из спеки: `take` max=1000, min=1, default=100; `includeRemoved` default=False
- **`PUT /manufactures/{manufactureId}/stock-items/available/{stockItemId}`** — Мануфактуры → Добавление доступного сырья
  - scopes: `accounting:manufacture`
- **`DELETE /manufactures/{manufactureId}/stock-items/available/{stockItemId}`** — Мануфактуры → Удаление доступного сырья
  - scopes: `accounting:manufacture`
- **`GET /manufactures/{manufactureId}/orders`** — Мануфактуры → Заказы кофеен
  - scopes: `accounting:manufacture`
  - границы из спеки: `take` max=100, min=1, default=10; `statuses` enum=Created|Confirmed|Sent|Delivered
- **`POST /manufactures/orders/{orderId}/confirm`** — Мануфактуры → Подтверждение заказа внешней мануфактурой
  - scopes: `accounting:manufacture`
- **`POST /manufactures/orders/{orderId}/send`** — Мануфактуры → Отправка заказа кофейне
  - scopes: `accounting:manufacture`
- **`GET /manufactures/{manufactureId}/delivered`** — Мануфактуры → Факт приёма заказов
  - scopes: `accounting:manufacture`
  - границы из спеки: `take` max=100, min=1, default=10

### Auth API (`auth-api.yaml`)

- **`GET /roles/catalog`** — Auth → Каталог ролей пользователей
  - scopes: `user.role:read`
- **`GET /users/feed/updates`** — Auth → Обновления
  - scopes: `users:feed`
  - границы из спеки: `skip` max=2147483647, min=0, default=0; `take` max=100, min=1, default=10
- **`GET /users/feed/deletions`** — Auth → Удаления
  - scopes: `users:feed`
  - границы из спеки: `skip` max=2147483647, min=0, default=0; `take` max=100, min=1, default=10
- **`GET /roles/list`** — Auth → Список ролей
  - scopes: `user.role:read`
- **`GET /roles/units`** — Auth → Юниты пользователя
  - scopes: `user.role:read`

### Customer Rating API (`controlling-api.yaml`)

- **`GET /ratings/customer-experience`** — Рейтинги → Рейтинг клиентского опыта
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /ratings/standards`** — Рейтинги → Рейтинг стандартов
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /ratings/customer-experience/detalization`** — Рейтинги → Детальный рейтинг клиентского опыта пиццерии
  - scopes: `shared`
- **`GET /ratings/standards/detalization`** — Рейтинги → Детальный рейтинг стандартов пиццерии
  - scopes: `shared`
- **`GET /ratings/customer-experience/history`** — Рейтинги → История рейтинга клиентского опыта
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /ratings/standards/history`** — Рейтинги → История рейтинга стандартов
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /ratings/standards/appeals`** — Рейтинги → Апелляции рейтинга стандартов пиццерии
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /ratings/customer-experience/appeals`** — Рейтинги → Апелляции рейтинга клиентского опыта пиццерии
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /ratings/customer-experience/remarks`** — Рейтинги → Замечания рейтинга клиентского опыта пиццерии
  - scopes: `shared`
  - границы из спеки: `unit` maxLen=32; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /ratings/customer-experience/vitals`** — Рейтинги → Ключевые показатели рейтинга клиентского опыта пиццерии
  - scopes: `shared`
  - границы из спеки: `unit` maxLen=32
- **`GET /ratings/customer-experience/violations`** — Рейтинги → Топ нарушений рейтинга клиентского опыта пиццерии
  - scopes: `shared`
  - границы из спеки: `unit` maxLen=32; `top` max=100, min=1, default=5
- **`GET /ratings/customer-experience/criterion-violations`** — Рейтинги → Топ критериев с нарушениями рейтинга клиентского опыта пиццерии
  - scopes: `shared`
  - границы из спеки: `unit` maxLen=32; `top` max=100, min=1, default=5
- **`GET /ratings/customer-experience/metrics/history`** — Рейтинги → История метрики рейтинга клиентского опыта
  - scopes: `shared`
  - границы из спеки: `unit` maxLen=32; `metric` maxLen=32
- **`GET /ratings/standards/remarks`** — Рейтинги → Замечания рейтинга стандартов пиццерии
  - scopes: `shared`
  - границы из спеки: `unit` maxLen=32; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /ratings/standards/vitals`** — Рейтинги → Ключевые показатели рейтинга стандартов пиццерии
  - scopes: `shared`
  - границы из спеки: `unit` maxLen=32
- **`GET /ratings/standards/violations`** — Рейтинги → Топ нарушений рейтинга стандартов пиццерии
  - scopes: `shared`
  - границы из спеки: `unit` maxLen=32; `top` max=100, min=1, default=5
- **`GET /ratings/standards/criterion-violations`** — Рейтинги → Топ критериев с нарушениями рейтинга стандартов пиццерии
  - scopes: `shared`
  - границы из спеки: `unit` maxLen=32; `top` max=100, min=1, default=5
- **`GET /ratings/standards/metrics/history`** — Рейтинги → История метрики рейтинга стандартов
  - scopes: `shared`
  - границы из спеки: `unit` maxLen=32; `metric` maxLen=32
- **`GET /checkups`** — Проверки → Список проверок
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /checkups/{checkupId}`** — Проверки → Детали проверки
  - scopes: `shared`

### Dodo IS API (`dodo-is-api-v2.yaml`)

- **`GET /production/orders-handover-time`** — Производство → Время выдачи заказа
  - scopes: `productionefficiency`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /orders`** — Заказы → Заказы по идентификаторам
  - scopes: `orders`, `user.role:read`
  - границы из спеки: `orderIds` maxItems=100
- **`GET /orders/clients-statistics`** — Заказы → Статистика по новым клиентам
  - scopes: `orders`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /production/tracking-metrics/summary`** — Производство → Метрики с трекинга (Сводные)
  - scopes: `productionefficiency`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /production/orders-handover-statistics`** — Производство → Статистика выдачи заказов
  - scopes: `productionefficiency`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /production/productivity`** — Производство → Производительность
  - scopes: `productionefficiency`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /production/stop-sales-channels`** — Производство → Стоп-продажи по каналам продаж
  - scopes: `stopsales`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /production/stop-sales-ingredients`** — Производство → Стоп-продажи по ингредиентам
  - scopes: `stopsales`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /production/stop-sales-products`** — Производство → Стоп-продажи по продуктам
  - scopes: `stopsales`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /production/unit-workload-by-orders`** — Производство → Нагрузка на заведение по заказам
  - scopes: `productionefficiency`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100; `salesChannels` enum=Dine-in|Takeaway|Delivery|Tracker|StaffMeal; `productCategories` enum=Pizza|Drinks|Snacks|Sauces|Goods|Desserts|Pieces; `orderSources` enum=CallCenter|Website|Dine-in|MobileApp|Manager|Aggregator|Kiosk; `periodIntervalInMinutes` enum=15|30|60, default=60; `bakedOnly` default=false
- **`GET /production/unit-workload-by-products`** — Производство → Нагрузка на заведение по продуктам
  - scopes: `productionefficiency`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100; `salesChannels` enum=Dine-in|Takeaway|Delivery|Tracker|StaffMeal; `productCategories` enum=Pizza|Drinks|Snacks|Sauces|Goods|Desserts|Pieces; `orderSources` enum=CallCenter|Website|Dine-in|MobileApp|Manager|Aggregator|Kiosk; `bakedOnly` default=false
- **`GET /delivery/statistics`** — Доставка → Статистика
  - scopes: `deliverystatistics`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /delivery/vouchers`** — Доставка → Сертификаты за опоздание
  - scopes: `deliverystatistics`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /delivery/efficiency`** — Доставка → Эффективность
  - scopes: `deliverystatistics`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /delivery/couriers-orders`** — Доставка → Заказы курьеров
  - scopes: `deliverystatistics`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /delivery/delivery-sectors`** — Доставка → Сектора доставки
  - scopes: `deliverystatistics`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /delivery/stop-sales-sectors`** — Доставка → Стоп-продажи по секторам
  - scopes: `stopsales`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /staff/members`** — Команда → Список сотрудников
  - scopes: `staffmembers:read`, `user.role:read`
  - границы из спеки: `staffType` enum=Operator|KitchenMember|Courier|Cashier|PersonalManager; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`POST /staff/incentives/premium/one-time`** — Команда → Премия единоразовая
  - scopes: `incentives`, `user.role:read`
- **`POST /staff/incentives/premium/hourly`** — Команда → Премия к вознаграждению в час
  - scopes: `incentives`, `user.role:read`
- **`POST /staff/incentives/premium/position`** — Команда → Премия по должности к вознаграждению в час
  - scopes: `incentives`, `user.role:read`
- **`GET /staff/incentives/premium`** — Команда → Премии
  - scopes: `incentives`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /staff/members/search`** — Команда → Поиск сотрудников
  - scopes: `staffmembersearch`, `user.role:read`
- **`GET /staff/members/birthdays`** — Команда → Поиск дней рождения сотрудников
  - scopes: `staffmembers:read`, `user.role:read`
  - границы из спеки: `dayFrom` max=31, min=1; `monthFrom` max=12, min=1; `monthTo` max=12, min=1; `skip` min=0, default=0; `take` max=100, min=0, default=100; `dayTo` max=31, min=1
- **`GET /staff/members/{id}`** — Команда → Информация о сотруднике
  - scopes: `staffmembers:read`, `user.role:read`
  - границы из спеки: `findby` enum=staffid|userid, default=staffid
- **`POST /staff/members/{id}/suspend`** — Команда → Отстранение сотрудника
  - scopes: `staffmembers:write`, `user.role:read`
- **`GET /staff/members/directors`** — Команда → Контакты руководителей
  - scopes: `staffmembers:read`, `user.role:read`
  - границы из спеки: `directorType` enum=DepartmentDirector|UnitDirector|ScheduleManager|HRManager|CommunicationsManager|MarketingManager|SupplyManager; `skip` min=0, default=0; `take` max=1000, min=0, default=100; `userId` maxLen=32
- **`GET /staff/shifts`** — Команда → Смены сотрудников (по пиццериям)
  - scopes: `staffshifts:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `staffTypeName` enum=Operator|KitchenMember|Courier|Cashier|PersonalManager; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /staff/members/shifts`** — Команда → Смены сотрудников (по идентификаторам)
  - scopes: `staffshifts:read`, `user.role:read`
  - границы из спеки: `staffIds` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`POST /staff/shifts/clock-out`** — Команда → Закрытие смены
  - scopes: `staffshifts:write`, `user.role:read`
- **`GET /staff/couriers-on-shift`** — Команда → Курьеры на смене
  - scopes: `staffshifts:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /staff/schedules`** — Команда → Расписания
  - scopes: `staffshifts:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100; `staffType` enum=Operator|KitchenMember|Courier|Cashier|PersonalManager
- **`POST /staff/schedules`** — Команда → Расписания (создание)
  - scopes: `staffshifts:write`, `user.role:read`
- **`DELETE /staff/schedules/{id}`** — Команда → Расписания (удаление)
  - scopes: `staffshifts:write`, `user.role:read`
- **`GET /staff/schedules/forecast`** — Команда → Расписания: прогнозные метрики
  - scopes: `staffshifts:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /staff/positions`** — Команда → Должности сотрудников
  - scopes: `shared`
- **`GET /staff/positions/history`** — Команда → История должностей сотрудников
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /staff/schedules/availability-periods`** — Команда → Карта возможностей
  - scopes: `staffmembers:read`, `user.role:read`
- **`GET /staff/incentives-by-members`** — Команда → Вознаграждения (новое)
  - scopes: `incentives`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `staffTypes` enum=Operator|KitchenMember|Courier|Cashier|PersonalManager
- **`GET /staff/vacancies/count`** — Команда → Количество открытых вакансий
  - scopes: `shared`
  - границы из спеки: `take` max=1000, min=1, default=100; `skip` min=0, default=0; `businessId` maxLen=32
- **`GET /staff/vacancies`** — Команда → Открытые вакансии
  - scopes: `shared`
  - границы из спеки: `take` max=1000, min=1, default=100; `skip` min=0, default=0; `staffTypes` enum=Operator|KitchenMember|Courier|Cashier|PersonalManager; `businessId` maxLen=32
- **`GET /organization-structure/legal-entities`** — Оргструктура → Список юрлиц
  - scopes: `organizationstructure`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /organization-structure/legal-entity-types`** — Оргструктура → Список типов юрлиц
  - scopes: `organizationstructure`
- **`GET /organization-structure/localities`** — Оргструктура → Список населённых пунктов
  - scopes: `localities:read`
- **`GET /accounting/suppliers`** — Учет → Список поставщиков
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /accounting/dough-consumption`** — Учет → Расход теста
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /accounting/local-suppliers`** — Учет → Список локальных поставщиков
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100; `units` maxItems=30
- **`GET /accounting/products`** — Учёт → Продукты
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100; `excludeInactive` default=False; `includeRemoved` default=false
- **`GET /accounting/products/{id}`** — Учёт → Информация о продукте
  - scopes: `accounting:read`, `user.role:read`
- **`GET /accounting/stock-items`** — Учёт → Сырьё
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /accounting/local-stock-items`** — Учёт → Локальное сырьё
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100; `units` maxItems=30
- **`GET /accounting/semi-finished-products-production`** — Учёт → Произведенные полуфабрикаты
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /accounting/write-offs/products`** — Учёт → Списанные продукты
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`POST /accounting/write-offs`** — Учёт → Списание
  - scopes: `accounting:read`, `user.role:read`
- **`GET /accounting/write-offs/stock-items`** — Учёт → Списанное сырье
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /accounting/defective-products`** — Учёт → Забракованные продукты
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /accounting/staff-meals`** — Учёт → Питание команды
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /accounting/cancelled-sales`** — Учёт → Отмены заказов
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /accounting/sales`** — Учёт → Продажи
  - scopes: `sales`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `salesChannel` enum=Dine-in|Takeaway|Delivery; `skip` min=0, default=0; `take` max=1000, min=0, default=100; `orderSource` enum=CallCenter|Website|Dine-in|MobileApp|Manager|Aggregator|Kiosk
- **`GET /accounting/stock-consumptions-by-period`** — Учёт → Расход сырья за период
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /accounting/stock-transfers`** — Учёт → Перемещения сырья
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `statuses` enum=Created|Ordered|Shipped|Received|Cancelled; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /accounting/incoming-stock-items`** — Учёт → Приходы сырья
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100; `units` maxItems=30
- **`GET /accounting/incoming-stock-items/by-supply`** — Учёт → Приходы сырья по поставке
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `skip` min=0, default=0; `take` max=100, min=0, default=10; `units` maxItems=30
- **`GET /accounting/inventory-stocks`** — Учёт → Складские остатки
  - scopes: `accounting:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0; `take` max=1000, min=0; `categories` enum=Ingredient|SemiFinishedProduct|FinishedProduct|Packing; `includeUnconfirmed` default=False
- **`POST /supplies/receipts`** — Поставки → Поступление
  - scopes: `receipts:write`, `user.role:read`
- **`GET /units/shifts`** — Заведения → Смены заведений
  - scopes: `unit:read`, `unitshifts:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30; `skip` min=0, default=0; `take` max=1000, min=0, default=100
- **`GET /units`** — Заведения → Информация о заведениях
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100; `unitTypes` enum=Office|Store|CallCenter|Warehouse|DeliveryService|PrepackedProductsFactory|ProductionDistributionWorkshop|ProductionZone; `unitStates` enum=Open|Close|TemporaryClosed
- **`GET /units/stores`** — Заведения → Информация о пиццериях/кофейнях
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100; `unitStates` enum=Open|Close|TemporaryClosed
- **`GET /units/distributioncenters`** — Заведения → Информация о ПРЦ
  - scopes: `shared`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100; `unitStates` enum=Open|Close|TemporaryClosed
- **`GET /units/work-stations`** — Заведения → Производственные станции
  - scopes: `shared`, `unit:read`, `user.role:read`
  - границы из спеки: `units` maxItems=30
- **`GET /units/month-goals`** — Заведения → Цели на месяц
  - scopes: `unit:read`, `user.role:read`
  - границы из спеки: `unit` maxLen=32; `year` min=2000; `month` max=12, min=1
- **`PATCH /units/month-goals`** — Заведения → Цели на месяц
  - scopes: `unit:write`, `user.role:read`
- **`GET /finances/sales/country/daily`** — Финансы → Дневные продажи по стране
  - scopes: `shared`
- **`GET /finances/sales/units/daily`** — Финансы → Дневные продажи по заведениям
  - scopes: `shared`
  - границы из спеки: `units` maxItems=30
- **`GET /finances/sales/country/monthly`** — Финансы → Месячные продажи по стране
  - scopes: `shared`
- **`GET /finances/sales/units/monthly`** — Финансы → Месячные продажи по заведениям
  - scopes: `shared`
  - границы из спеки: `units` maxItems=30
- **`GET /finances/sales/country`** — Финансы → Продажи по стране за период
  - scopes: `shared`
- **`GET /finances/sales/units`** — Финансы → Продажи по заведениям за период
  - scopes: `shared`
  - границы из спеки: `units` maxItems=30

### Сети заведений (`franchisee-api.yaml`)

- **`GET /units`** — Заведения → Сети
  - scopes: `franchisee:read`
  - границы из спеки: `businessId` maxLen=32; `unitStates` enum=Open|Close, default=Open

### Inventory API (`inventory-api.yaml`)

- **`GET /revisions`** — Учет → Ревизии
  - scopes: `accounting`
  - границы из спеки: `skip` min=0; `take` max=1000, min=0, maxLen=1000; `typeOfPeriodicity` enum=Day|Week|Month

### IoT API (`iot-api.yaml`)

- **`POST /telemetry/v1/device/register`** — IoT → Регистрация устройства
  - scopes: `iot.partnerDevice`
- **`POST /telemetry/v1/measurement`** — IoT → Добавление измерения
  - scopes: `iot.partnerDevice`
- **`GET /telemetry/v1/device/{id}`** — IoT → Получение устройства
  - scopes: `iot.partnerDevice`
- **`PUT /telemetry/v1/device/update`** — IoT → Обновление устройства
  - scopes: `iot.partnerDevice`
- **`GET /telemetry/v1/device/find`** — IoT → Поиск устройства
  - scopes: `iot.partnerDevice`
  - границы из спеки: `unitId` maxLen=32

### Label printer API (`label-printer-api.yaml`)

- **`GET /queue`** — Принтер этикеток → Очередь этикеток на печать
  - scopes: `labelprinter_api`
  - границы из спеки: `units` maxItems=30
- **`POST /confirmation`** — Принтер этикеток → Подтверждение печати этикетки
  - scopes: `labelprinter_api`

### Магазин приложений (`marketplace-api.yaml`)

- **`GET /subscriptions`** — Подписки → Активные подписки пользователя в приложении
  - scopes: `marketplacesubscription:read`, `user.role:read`
- **`GET /invoices`** — Бухгалтерия → Счета на оплату
  - scopes: `marketplaceinvoice:read`
  - границы из спеки: `month` max=12, min=1
- **`POST /invoices`** — Бухгалтерия → Счета на оплату
  - scopes: `marketplaceinvoice:write`
*Свои серверы:* `https://api.dodois.io/store-api`, `https://api.dodois.com/store-api`
- **`GET /storage`** — Store API → Последние данные
  - scopes: `storeapi:read`
  - границы из спеки: `modelId` maxLen=32; `units` maxLen=320; `take` max=100, min=1, default=10
- **`POST /storage`** — Store API → Загрузка данных
  - scopes: `storeapi:write`

### Pos Payments API (`pospayments.yaml`)

- **`GET /accounting-report`** — Данные для банковской выписки по датам
  - scopes: `pospayments:read`
  - границы из спеки: `paymentProvider` enum=ibox

### Customer Rating API (`ratings-api.yaml`)

*Свои серверы:* `https://api.dodois.io/dodopizza/customer-feedback`, `https://api.dodois.com/dodopizza/customer-feedback`, `https://api.dodois.io/drinkit/customer-feedback`, `https://api.dodois.com/drinkit/customer-feedback`
- **`GET /recent-feedbacks`** — Отзывы клиентов → Недавние отзывы
  - scopes: `shared`
  - границы из спеки: `units` maxLen=956; `includeFeedbacksWithEmptyComment` default=False
*Свои серверы:* `https://api.dodois.io/customer-feedback`, `https://api.dodois.com/customer-feedback`, `https://api.dodois.io/customer-feedback`, `https://api.dodois.com/customer-feedback`
- **`GET /customer-ratings`** — Отзывы клиентов → Рейтинг клиентов
  - scopes: `shared`
  - границы из спеки: `units` maxLen=3231
*Свои серверы:* `https://api.dodois.io/customer-feedback`, `https://api.dodois.com/customer-feedback`, `https://api.dodois.io/customer-feedback`, `https://api.dodois.com/customer-feedback`
- **`GET /lfl/by-units`** — LFL → LFL по заведениям
  - границы из спеки: `units` maxLen=990; `granularity` enum=Day|Week|Month, default=Day
*Свои серверы:* `https://api.dodois.io/customer-feedback`, `https://api.dodois.com/customer-feedback`, `https://api.dodois.io/customer-feedback`, `https://api.dodois.com/customer-feedback`
- **`GET /lfl/by-countries`** — LFL → LFL по странам
  - границы из спеки: `countries` maxLen=300; `granularity` enum=Day|Week|Month, default=Day

### Staff API (`staff-api.yaml`)

- **`GET /recruitment/responses`** — Найм → Отклики на вакансии
  - scopes: `recruitment:read`
  - границы из спеки: `skip` min=0, default=0; `take` max=1000, min=0, default=100

Всего операций в спеках: 146.

