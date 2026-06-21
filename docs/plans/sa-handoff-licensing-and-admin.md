# Хендофф для сессии-владельца `dodotool-sa`: лицензирование + админ-фронт + разделение

Что сделано в этой сессии (на VPS `94.26.246.138`, репо `/home/ask/dodotool-sa`)
и что осталось применить владельцу. Связанная спека модели:
`sa-licensing-capabilities-spec.md` (рядом).

---

## 1. Бэкенд — ГОТОВО на ветке `feat/capability-licensing`

4 коммита поверх `main`, **не запушено** (на VPS нет github-ключа), **не
смержено, не задеплоено**. 9 capability-тестов + регрессия admin/direct-licenses
зелёные; миграция 0008 применяется на чистой БД.

```
d?????  GET /admin/license-modules — список модулей для мультиселекта
d4391f0 direct-лицензии с модулями (extensions): admin endpoint, схемы, crud
947f377 require_capability гейт + /entitlements + unit_entitlement
0dfa701 capability-модель: каталог, tariff_capabilities, резолвер (+миграция 0008)
```

**Что добавилось (API-контракт для фронта/модулей):**
- `app/models.py`: модель `TariffCapability(alias, kind['tariff'|'extension'], capability)`.
- `app/capabilities.py`: каталог `CAPABILITIES` (finance.*/pulse.*(Пульс)/kassa.*),
  `unit_capabilities`, `unit_entitlement`, `has_capability`, `DEFAULT_CAPABILITIES`.
- `alembic 0008`: таблица `tariff_capabilities` + сид маппинга (26 строк).
- `subscription_gate.require_capability("finance")` — аддитивно; бинарный
  `require_active_subscription` НЕ тронут (обратная совместимость).
- `GET /entitlements` — per-unit активные capabilities + `expires_at` (для
  pnl/кассы: гейт разделов и «Подключить»).
- `POST /admin/licenses` теперь принимает `extensions: list[str]` (модули);
  `SubscriptionOut` отдаёт `extensions`.
- `GET /admin/license-modules` → `[{alias, capabilities[], label}]` — для
  мультиселекта в админке (только `extension`-алиасы).

**Что сделать владельцу:**
1. Ревью `git diff main..feat/capability-licensing`.
2. Merge в `main` → push → деплой вашим штатным флоу (с dev-машины по SSH).
   Миграция применится сама: сервис `migrate` (`alembic upgrade head`) в
   `docker-compose.prod.yml` выполняется до старта `api`.
3. После деплоя пересобрать `openapi.json` (`scripts/dump_openapi.py`) — фронту
   нужны свежие типы.

> До деплоя лицензии выдавать нельзя (таблицы `tariff_capabilities`/полей
> `extensions` ещё нет в работающем образе). После деплоя — через админку (см.
> §2) или напрямую через API/CRUD.

---

## 2. Фронт `/admin` (`apps/dodotool-admin`) — ДЕЛЬТА (исходники у владельца локально)

Экран лицензий уже есть (клиенты → «Выдать» с чекбоксами заведений + срок →
`POST /admin/licenses`; список лицензий с Продлить/Отозвать). Нужно добавить
**выбор модулей** при выдаче.

**Изменения:**
1. **Регенерация типов**: `openapi:gen` из обновлённого `openapi.json`
   (`DirectLicenseCreate.extensions`, `SubscriptionOut.extensions`,
   новый `GET /admin/license-modules`).
2. **Хук** `useLicenseModules` (`src/hooks/queries.ts`): `GET /api/admin/license-modules`
   → `[{alias, capabilities, label}]`.
3. **Модалка «Выдать»** (`src/screens/LicensesScreen.tsx`): добавить
   `MultiSelect` «Модули» (Mantine), `data` = модули из хука (`value=alias`,
   `label=label`). На submit — `POST /api/admin/licenses` с
   `extensions: selectedAliases` (вместе с units + expires_at). Желательно
   требовать ≥1 модуль (иначе бэкенд по safety-net даст только базы).
4. **Список выданных лицензий**: показать модули — `license.extensions`
   (или резолвить в labels через тот же `license-modules`-словарь).
5. Деплой фронта — как раньше: `rsync apps/dodotool-admin/dist/ → frontend-admin-dist/`.

**Контракт `POST /admin/licenses`** (тело):
```json
{ "owner_sub": "...", "units": ["<uuid>", ...],
  "expires_at": "2026-07-01T00:00:00Z",
  "tariff_alias": "direct", "extensions": ["finance", "pulse", "orders"] }
```
`extensions` — это `alias` из `GET /admin/license-modules`.

---

## 3. Разделение: вынос кассы из `sa_backend` → чистый `sa` (план, отдельный шаг)

Репо уже называется `sa_backend`; правильное движение — **вынести кассовый
домен в отдельный репо/поддомен**, а `sa` оставить нейтральным
(auth + identity + tenancy + licensing). Дешевле всего сейчас (в кассе-new нет
пользователей).

**Граница:**
- **Остаётся в `sa`** (платформенное ядро): auth/сессии/OAuth Dodo
  (`dodois_oauth`, `dodois_auth`, `dodois_credentials`, `token_refresh`),
  identity (`/me`, `auth.py`, `permissions`), tenancy (`franchisees`,
  `projects`/юниты, `onboarding`), лицензирование/capabilities (`subscriptions`,
  `subscription_*`, `capabilities`, `tariff_capabilities`, `/admin/*`,
  `/entitlements`). Сюда же — будущий **токен-брокер** для модулей.
- **Уезжает в кассу** (модуль): `accounts`, `operations`(+audit/attachments),
  `shifts`, `courier`, `categories`, `calculator`, `balances`, `revenue_sync`,
  кассовые поля `project_settings` (revenue category ids и пр.).

**Шаги:**
1. Зафиксировать границу и контракт `касса → sa` (касса ссылается на
   `project_id`/`dodois_uuid`; за токеном/идентичностью/правами ходит в `sa`).
2. Решить владение БД: общая БД с явным владением схем ИЛИ две БД + кросс-
   сервисные ссылки.
3. Вынести кассовый код+таблицы в новый репо; в `sa` оставить ядро.
4. Касса потребляет `sa`: SSO (кука `.dodotool.ru` + `/me`), `/entitlements`,
   токен-брокер. Гейт модулей — `require_capability("kassa")`.

Делать ПОСЛЕ приземления лицензирования; до подключения pnl и до пользователей
в кассе.

---

## 4. Дальше для pnl (наш репо, отдельно)

pnl (Финансы/Пульс) становится потребителем `sa`: гибридный токен-резолвер
(`sa`-брокер → иначе legacy `dodois_credentials`), SSO через `/me`, гейт
разделов по `/entitlements`. Это в репозитории pnl, не в `sa`.
