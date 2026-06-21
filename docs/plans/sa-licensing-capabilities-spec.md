# Спецификация: capability-лицензирование в `dodotool-sa`

Инструкция для сессии-владельца репозитория `dodotool-sa` (на VPS `94.26.246.138`,
`/home/ask/dodotool-sa`). Цель: сделать лицензию модульной — она даёт конкретные
**возможности (capabilities)** на конкретные **заведения**, чтобы продавать по
частям и через маркетплейс, и напрямую (direct). Всё **аддитивно и обратно-
совместимо**: текущий бинарный гейт (`require_active_subscription`) продолжает
работать.

## Что уже есть (не трогаем, опираемся)

- `Subscription(source[marketplace|direct], franchisee_id, owner_sub, tariff_alias,
  extensions JSONB, expires_at, units→SubscriptionUnit[dodois_uuid])`.
- `subscription_sync.reconcile_subscription_status` → `project.subscription_status`
  = active/archived по `is_unit_covered` (бинарно, любой источник).
- Гейт `subscription_gate.require_active_subscription(project_id)` → 402 если
  `archived`. **Оставляем как есть.**
- `create_direct_license(...)` — ручная лицензия (source=direct, tariff_alias="direct").
- marketplace-синк кладёт `tariff_alias` + `extensions` из Marketplace API.

## Модель возможностей (гранулярно, с дроблением)

Коды capability — иерархические строки `module` / `module.feature`. Стартовый
каталог (расширяемый; точный состав под-фич — продуктовое решение):

- **finance** (Финансы / P&L): `finance`, `finance.forecast`, `finance.orders`,
  `finance.targets`, `finance.export`, `finance.custom_metrics`
- **pulse** (Пульс — бывш. Табло, сводка дня): `pulse`, `pulse.ops`,
  `pulse.stops_history`
- **kassa** (Касса): `kassa`, `kassa.courier`, `kassa.shifts`

Каталог — константа в коде (`app/capabilities.py`), напр. `CAPABILITIES: dict[str,
str]` (code→человеческое имя) + хелпер `module_of(cap) = cap.split(".")[0]`.

## Маппинг «упаковка → возможности» (data-driven)

Новая таблица — чтобы менять тарифы/цены без деплоя:

```
tariff_capabilities(
  alias       TEXT  NOT NULL,                 -- значение tariff_alias или extension-алиаса
  kind        TEXT  NOT NULL,                 -- 'tariff' | 'extension'
  capability  TEXT  NOT NULL,                 -- код из каталога
  PRIMARY KEY (alias, kind, capability)
)
```

Один и тот же словарь обслуживает **оба** источника: marketplace кладёт
`tariff_alias`/`extensions` из API, direct — админ выбирает их из того же набора.
Сид (пример, финальный — за продуктом):

- tariff `pro`     → finance, finance.*, pulse, pulse.*  (всё)
- tariff `finance` → finance, finance.forecast, finance.orders, finance.targets, finance.export
- tariff `pulse`   → pulse, pulse.ops, pulse.stops_history
- extension (à-la-carte, 1:1): `forecast`→finance.forecast, `orders`→finance.orders,
  `ops`→pulse.ops, `stops_history`→pulse.stops_history, и т.п.

**Сетка безопасности на время раскатки:** конфиг `DEFAULT_CAPABILITIES` (напр.
`{finance, pulse, kassa}` — базы модулей). Применяется, если у подписки
`tariff_alias`/`extensions` не дали ни одной capability (старые/немапленные
лицензии) — чтобы НИКТО не потерял доступ при включении. Убрать, когда весь
текущий парк лицензий смаплен.

## Резолвер (новый `app/capabilities.py` или в `crud/subscriptions.py`)

```python
def subscription_capabilities(db, sub) -> set[str]:
    caps = _lookup(db, sub.tariff_alias, "tariff")
    for ext in (sub.extensions or []):
        caps |= _lookup(db, ext, "extension")
    return caps or set(DEFAULT_CAPABILITIES)   # safety net на раскатку

def unit_capabilities(db, dodois_uuid, now=None) -> set[str]:
    now = now or datetime.now(timezone.utc)
    caps = set()
    for sub in active_subs_covering_unit(db, dodois_uuid, now):  # expires_at > now
        caps |= subscription_capabilities(db, sub)
    return caps
```

`active_subs_covering_unit` — join `subscription_units` × `subscriptions` по
`dodois_uuid` с `expires_at > now` (любой source).

## Гейт по возможности (`app/subscription_gate.py`, аддитивно)

```python
def require_capability(capability: str):
    def dep(project_id: int, db: DB) -> None:
        project = projects_crud.get_project(db, project_id)
        if project is None: raise HTTPException(404, "Project not found")
        caps = unit_capabilities(db, project.dodois_uuid)
        # базовый модуль или сама фича
        if capability not in caps and module_of(capability) not in caps:
            raise HTTPException(402, f"Capability '{capability}' not licensed")
    return Depends(dep)
```

`require_active_subscription` оставляем без изменений (обратная совместимость).
Эндпоинты модулей навешивают `require_capability("kassa")` и т.п. по мере надобности.

## Entitlements API (для модулей-потребителей: pnl/касса-фронт)

Расширить `GET /me` (или новый `GET /entitlements`): вернуть по юнитам активные
возможности — чтобы pnl/касса гейтили разделы и рисовали «Подключить».

```json
{ "units": [ { "dodois_uuid": "...", "capabilities": ["finance","finance.orders","pulse"],
               "expires_at": "2026-07-01T00:00:00Z" } ] }
```

Для текущего пользователя берём его юниты (через roles/dodois_credentials) и для
каждого зовём `unit_capabilities`.

## Direct-лицензии (`app/routers/admin.py` + `crud/subscriptions.create_direct_license`)

Расширить создание прямой лицензии, чтобы админ задавал **модули**:

```
POST /admin/licenses
{ owner_sub, franchisee_id?, units: [dodois_uuid], expires_at,
  tariff_alias?: str, extensions?: [str] }   # из того же словаря, что marketplace
```

`create_direct_license(..., tariff_alias, extensions)` — пробросить `extensions`
и произвольный `tariff_alias` (сейчас хардкод "direct"). Резолвер даст те же
capability, что и у marketplace с такими же alias/extensions. Так прямые продажи
и маркетплейс ходят через один путь.

## Миграция (alembic)

1. `tariff_capabilities` (см. DDL выше) + сид дефолтного маппинга (data-migration
   или `scripts/seed_capabilities.py`).
2. Схему `Subscription`/`SubscriptionUnit` НЕ меняем (используем существующие
   `tariff_alias`/`extensions`).

## Тесты (промежуточные гейты)

- `subscription_capabilities`: tariff/extension → ожидаемый набор; немапленное →
  `DEFAULT_CAPABILITIES`.
- `unit_capabilities`: объединение по нескольким активным подпискам; истёкшие
  исключены; разные источники складываются.
- `require_capability`: есть/нет capability → проходит/402; базовый модуль
  открывает фичи модуля.
- direct-лицензия с `extensions=["orders"]` → у юнита появляется `finance.orders`.
- **обратная совместимость**: `require_active_subscription` не изменился;
  существующие (немапленные) подписки сохраняют доступ через `DEFAULT_CAPABILITIES`.
- `/entitlements` (или `/me`): корректные per-unit capabilities.

## Порядок и обратная совместимость

1. Миграция + сид маппинга + каталог + резолвер (поведение не меняется — гейт
   ещё бинарный).
2. `/entitlements` + `require_capability` (аддитивно; вешаем на эндпоинты по мере
   готовности модулей).
3. Расширить direct-лицензии модулями.
4. Когда весь парк лицензий смаплен — убрать `DEFAULT_CAPABILITIES` safety net.

Нейминг: модуль сводки дня — **pulse (Пульс)**, не «board/Табло».
