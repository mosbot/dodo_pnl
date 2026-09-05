# SA → Касса: по вашему ответу от 2026-09-05 (вечер)

Всё, что вы просили, сделано сегодня; нумерация — ваша.

## Сделано

**Redis ACL сужен** до ваших префиксов, прод и стейдж одинаково:
`~entitlements:* ~dodois:* -@all +get +set +del +exists +expire +ttl +ping
+select +hello +info +client|setinfo +client|setname` (чуть шире вашего
минимума — `del/exists/expire/ttl` на случай инвалидации, `hello`/`client|
setinfo` шлёт redis-py при коннекте). Пароли и `REDIS_URL` не менялись.
Проверено под `kassa`: `GET dodois:members:*` / `SET entitlements:* EX` — OK,
`sa:*` и `KEYS` — NOPERM; оба контура health 200, в логах чисто.
Переименовывать ключи под `kassa:*` не нужно.

**CSP для kassa / stg-kassa** — ровно ваш список + `base-uri 'self';
object-src 'none'`, отдаётся Caddy на всех ответах домена:
```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self';
frame-ancestors 'none'; form-action 'self' https://sa.dodotool.ru;
base-uri 'self'; object-src 'none'
```
Прогнал оба домена headless-Chrome — CSP-нарушений в консоли нет.
`frame-ancestors 'none'` строже нашего `X-Frame-Options: SAMEORIGIN` — если
когда-нибудь захотите встраивать кассу во внешний фрейм хаба, скажете.

**`kassa_prod` — гранты выданы, `DATABASE_URL` записан** в
`~/dodotool-kassa/.env` (бэкап `.env.bak-kprod-<ts>`), пароль роли
пересоздан (старый нигде не хранился — извините за «notify.env», ошибся).
Права в БД `dodotool_sa`:
- ALL на кассовые: `accounts, categories, category_templates,
  kassa_network_policy, operations, operation_attachments,
  operation_audit_log, shifts, alembic_version_kassa` + их sequences;
  `CREATE` на схему `public` (ваш alembic сможет создавать новые таблицы);
- SELECT/INSERT/UPDATE на `projects, project_settings, franchisees,
  franchisee_settings, dodois_user_names` (+ `projects_id_seq`);
- SELECT на `dodois_credentials, subscriptions, subscription_units,
  tariff_capabilities`; будущие платформенные таблицы sa — SELECT по
  default privileges;
- к `pnl_service` — FATAL (проверено); `DELETE` на `subscriptions` — permission
  denied (проверено).
Если у вас где-то остался код, который пишет в `franchisees`/`projects`/
`dodois_credentials` (в crud есть `Franchisee(`, `DodoisCredentials(`) — INSERT
разрешён, DELETE нет; если увидите `permission denied` — пришлите таблицу и
операцию, добавлю. Пересоздавайте API и проверяйте, когда удобно; до этого
контейнер работает на старом URL из своего окружения. Отдельная БД для кассы
на проде — не сейчас; вернёмся после публикации.

**`/entitlements` — проверено расчётом по подпискам в SA.** Активных
подписок 6, все резолвятся из `tariff_capabilities`, ни одна не падала в
`DEFAULT_CAPABILITIES`; сравнение «с дефолтом / без дефолта» по всем 74
юнитам в `subscription_units` — 0 расхождений. `kassa` сейчас у 8 юнитов
(подписки с `kassa`: PiX ×1, ×1, 6-юнитная `finance,pulse,kassa`, XFood ×2 —
с пересечениями), ещё 1 юнит только `finance,pulse`; остальные 65 юнитов
в `subscription_units` — по истёкшим подпискам (в основном XFood 55). **Safety-net снят
(sa `1188228`, выкачен)**: немапленный alias/extension = пусто (fail-closed)
+ warning в лог `subscription … не замаплены в tariff_capabilities`.
Замаплены: `kassa|finance|pulse|pro|test` (tariff), `kassa|finance|pulse`
(extension). Маркетплейсный alias тарифа кассы — `kassa`, попадает в мапу.

## Принято

П. 2 снят; п. 6 CORS — ок; п. 7/9 — ок, тесты на держателя — хорошо;
п. 13 — спасибо за разные авторы в моке. Celery-beat — да, наш.

## Ближайшие даты, чтобы не забыть

XFood: подписка `kassa` (2 юнита) истекает **2026-09-30**, `finance,pulse`
(3 юнита) — **2026-10-02**. После снятия safety-net истёкшая подписка =
нет доступа к модулю, без «мягкого» периода. Продление — через SA-админку.

## Открытое

- у вас: пересоздать API на `kassa_prod` и подтвердить; пустая базовая
  миграция;
- у нас: второй OAuth-клиент под карточку «Финансы+Пульс»; WAL-архив —
  ждёт хост.

## Дополнение: scope `marketplace` / grants — вопрос закрыт

Проверено 2026-09-05: scope `marketplace` у приложения назначен, но это
Service-Token-scope (`client_credentials`), а у `cnM4i` включён только
Authorization Code Flow → `unauthorized_client`. Нужен он только для
`POST /marketplace/statuses/grants` (репорт «доступ жив/отозван»); подписки
и юниты идут пользовательским токеном (`marketplacesubscription:read`) и
работают. Решение Андрея: **в Pyrus не идём**; если Dodo IS сочтёт grants
обязательными — скажут при модерации, тогда и включим. Пункт 1 из
«блокирует публикацию» снят.
