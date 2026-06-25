# Аудит безопасности платформы Dodotool — 2026-06-22

Полный аудит трёх кодовых баз (pnl / sa / frontend) + БД + инфраструктура VPS.
Метод: статический разбор кода (read-only) + проверка живой конфигурации на
проде по SSH. Эксплойты не запускались.

> Важно: локальный mount `dodotool_sa_backend` **устарел** относительно живого
> кода на VPS (`~/dodotool-sa`). Все sa-находки — по коду на VPS (он и работает).

---

## Сводка по приоритетам

| # | Severity | Где | Кратко |
|---|---|---|---|
| C1 | 🔴 Critical | pnl | Сброс пароля без проверки тенанта → кросс-тенантный захват аккаунта |
| H1 | 🟠 High | infra | `~/dodotool-sa/.env` читаем всеми (664) + на хосте есть др. пользователи → утечка секретов |
| H2 | 🟠 High | sa | OAuth access/refresh-токены Dodo IS хранятся в БД в открытом виде |
| H3 | 🟠 High | sa | Сравнение `X-Admin-Token` не constant-time (мастер-секрет платформы) |
| M1 | 🟡 Medium | infra | SSH разрешает вход по паролю (брутфорс) |
| M2 | 🟡 Medium | infra | Нет security-заголовков (HSTS/X-Frame-Options/CSP) ни на одном vhost |
| M3 | 🟡 Medium | infra | `/dev/*` проксируется наружу — от суперюзер-входа отделяет один флаг |
| M4 | 🟡 Medium | pnl | Enforcement лицензий fail-open + гейт не на всех finance-эндпоинтах |
| M5 | 🟡 Medium | sa | Кука `dt_session` на `.dodotool.ru` + `return_to` на любой сабдомен → большой blast radius |
| M6 | 🟡 Medium | sa | CORS `allow_origins=["*"]` на auth-сервисе |
| L1 | 🔵 Low | infra | `/api/internal/*` доступен из интернета (но под токеном) |
| L2 | 🔵 Low | admin SPA | Admin-токен в `localStorage` |
| L3 | 🔵 Low | pnl | SSO-кука `SameSite=Lax` vs `Strict` у пароля |
| L4 | 🔵 Low | pnl | Запись в targets/ops с произвольным project_id (внутри своего тенанта) |
| L5 | 🔵 Low | pnl | `SafeStaticFiles` — deny-list вместо allow-list; CSP `style-src 'unsafe-inline'` |

Активно эксплуатируемых дыр в текущем прод-конфиге не найдено (DEV_AUTH выкл,
internal/admin под токеном и не торчат без префикса `/api`, PKCE+state корректны,
SQLi нет). Главные структурные риски — C1, открытые секреты на хосте (H1/H2) и
широкий доверительный периметр сабдоменов (M5).

---

## CRITICAL

### C1 — Кросс-тенантный захват аккаунта через сброс пароля
**Где:** `app/auth/admin_router.py:255-272` (`POST /api/admin/users/{user_id}/reset-password`).
**Суть:** эндпоинт под `Depends(require_admin)` (любой админ), берёт `get_user_by_id(user_id)`
**без проверки, что юзер принадлежит тенанту вызывающего**, генерит новый пароль,
ставит его и **возвращает пароль в открытом виде** в ответе. Соседние мутации
(`admin_update_user`, `admin_delete_user`) правильно используют
`require_admin_for_user("user_id")` — здесь забыли.
**Эффект:** `network_admin` сети A передаёт `user_id` из сети B (или id супер-админа)
и получает рабочий пароль → захват чужого аккаунта и эскалация до super_admin.
Срабатывает, как только появится второй network_admin (обычный путь роста).
**Фикс (1 строка):** заменить зависимость на `Depends(require_admin_for_user("user_id"))`
(фабрика уже есть в `app/auth/dependencies.py:122`). Доп.: запрет сбрасывать пароль
`super_admin`, если вызывающий не super_admin. Добавить регресс-тест.

---

## HIGH

### H1 — `~/dodotool-sa/.env` доступен на чтение всем + на хосте есть другие пользователи
**Где:** VPS, `/home/ask/dodotool-sa/.env` режим `664` (`-rw-rw-r--`), каталог `755`.
На хосте интерактивные пользователи `misha` (uid 1001), `claude` (uid 1002).
**Эффект:** в файле `SA_INTERNAL_TOKEN`, OAuth client_secret (`cnM4i`), креды БД,
`SECRET_KEY`. Любой из этих пользователей может `cat` файл прямо сейчас → полная
утечка секретов, включая internal-токен брокера Dodo IS. (Для сравнения
`~/pnl-service/.env` корректно `600`.)
**Фикс:** `chmod 600 ~/dodotool-sa/.env` (+ `chmod 700 ~/dodotool-sa`). Считать
секреты потенциально утёкшими → ротировать `SA_INTERNAL_TOKEN` и OAuth client_secret,
если `misha`/`claude` не полностью доверенные.

### H2 — OAuth-токены Dodo IS хранятся в БД в открытом виде
**Где:** `app/crud/dodois_credentials.py:73-75` — `access_token`/`refresh_token`
пишутся в таблицу `dodois_credentials` без шифрования. Refresh-токены долгоживущие
(`offline_access`).
**Эффект:** чтение БД = возможность выдавать себя за любого франчайзи в Dodo IS.
Несогласованность: pnl шифрует PlanFact-ключи (Fernet), sa токены Dodo — нет.
**Фикс:** шифровать at-rest (Fernet/`pgcrypto`) ключом отдельным от `SESSION_SECRET`;
как минимум — Postgres не публикуется наружу (это уже так — хорошо) + дисковое
шифрование/ACL как компенсирующий контроль.

### H3 — Сравнение `X-Admin-Token` не constant-time
**Где:** VPS `app/admin_auth.py:20` — `if x_admin_token != config.ADMIN_API_TOKEN:`.
**Эффект:** теоретически timing-атака на мастер-секрет, который защищает
`/internal/dodois-token` (выдаёт живые токены Dodo IS) и `/admin/licenses`. По TLS+
сети практическая эксплуатация низкая, но фикс тривиален.
**Фикс:** `import hmac; if not hmac.compare_digest(x_admin_token or "", config.ADMIN_API_TOKEN): ...`

---

## MEDIUM

### M1 — SSH разрешает вход по паролю
**Где:** VPS `sshd -T` → `passwordauthentication yes`, порт 22 открыт миру (UFW).
**Эффект:** брутфорс паролей. Вход по ключу уже работает.
**Фикс:** `PasswordAuthentication no` + `KbdInteractiveAuthentication no`, reload sshd.
(root уже key-only — ок.)

### M2 — Нет security-заголовков на Caddy-vhost'ах
**Где:** `~/dodotool-sa/Caddyfile` (sa/pnl/app). Проба: нет HSTS, X-Frame-Options,
X-Content-Type-Options, Referrer-Policy, CSP.
**Эффект:** нет защиты от кликджекинга, MIME-sniffing, downgrade; нет CSP как
backstop для innerHTML-насыщенного фронта pnl (экранирование сейчас хорошее, но
одна пропущенная `esc()` в будущем = stored XSS без сдерживания).
**Фикс:** общий `header {}` блок на каждый vhost (HSTS, nosniff, X-Frame-Options DENY,
Referrer-Policy, CSP — учесть инлайн-скрипты в login.html/board.html → начать с
`'unsafe-inline'` или перейти на nonce).

### M3 — `/dev/*` проксируется наружу
**Где:** `Caddyfile` sa: `@backend path /api/* /dodois/* /dev/* /health`.
**Статус:** сейчас НЕ эксплуатируется — `DEV_AUTH=false`, `/dev/login` → 404
(роут не регистрируется). Но от суперюзер-входа над всеми тенантами отделяет
один env-флаг; в sa CLAUDE.md заложен Caddy IP-allowlist/basic-auth на `/dev/*` —
не реализован.
**Фикс:** убрать `/dev/*` из публичного `@backend` (или `@dev path /dev/*` → `respond 403`
для не-allowlist IP).

### M4 — Enforcement лицензий fail-open + неполный гейт
**Где:** `app/licensing.py:43-67` (любое исключение → `None`), `app/main.py`
`_require_capability` (вызывается только в `get_pnl` и `get_board`).
**Эффект:** (а) при `caps=None` (sa недоступен / нет юнитов / любая ошибка) гейт
пропускает — обход биллинга; (б) finance-данные доступны через необгейченные
`/api/pnl.xlsx`, `/api/operations(.xlsx)`, `/api/revenue-history`; board — через
`/api/board-metrics`, `/api/ops-metrics/*`. Конфиденциальность не страдает (всё
по-прежнему под auth + tenant-scope) — это полнота entitlement-контроля.
**Фикс:** различать «sa явно вернул пустой набор» (deny) и «sa ошибся» (grace);
кэшировать last-known-good и закрывать при unknown с коротким grace. Добавить
`_require_capability` на перечисленные эндпоинты.

### M5 — Широкий доверительный периметр сабдоменов (sa)
**Где:** `main.py:27` кука `dt_session` `domain=".dodotool.ru"`;
`routers/dodois_oauth.py:190` `return_to` regex `^https://[a-z0-9.-]+\.dodotool\.ru(/|$)`.
**Эффект:** кука уходит на **любой** `*.dodotool.ru`; XSS на любом сабдомене или
перехват «висящего»/неиспользуемого сабдомена → кража sa-сессии. `return_to`
доверяет всем сабдоменам (anchor `(/|$)` корректен — классический обход закрыт).
**Фикс:** allow-list реальных целей (`pnl.`, `app.`, `kassa.`) вместо «любой
сабдомен» — сужает и куку, и редирект. Инвентаризация DNS, без dangling-записей.

### M6 — CORS `allow_origins=["*"]` на auth-сервисе (sa)
**Где:** `main.py:31-36`. Смягчает то, что `allow_credentials` не выставлен →
браузер не шлёт куку при wildcard-CORS. Но header-based `X-Admin-Token`-путь не
зависит от куки.
**Фикс:** явный allow-list origin'ов (хосты SPA dodotool.ru). Никогда не сочетать
`*` с `allow_credentials=True`.

---

## LOW

- **L1 (infra):** `/api/internal/dodois-token` доступен из интернета (проба → 401 без токена). Защищён только `SA_INTERNAL_TOKEN`. В связке с H1 — опасно. Фикс: `@internal path /api/internal/*` → `respond 403`, оставить только во внутренней docker-сети.
- **L2 (admin SPA):** admin-токен в `localStorage` (`apps/dodotool-admin/src/api/token.ts`) → экфильтрация при любом XSS. Фикс: httpOnly-кука/короткоживущий сессионный токен.
- **L3 (pnl):** SSO-кука `SameSite=Lax` (`router.py:191`) vs `Strict` у пароля (`:144`). CSRF-контроль — `Strict`. Эндпоинты-мутации POST/PATCH/DELETE (на Lax cross-site не уходят), так что практический риск мал. Фикс: `Strict` (редирект после SSO — на локальный путь).
- **L4 (pnl):** записи `upsert_target`/`upsert_ops_metric`/`upsert_ops_project_target` принимают произвольный `project_id` без пересечения с allowed-set — но всё в рамках своего тенанта (`pf_key_id` форсится). Фикс (defense-in-depth): пересекать с `_resolve_project_filter`.
- **L5 (pnl):** `SafeStaticFiles` — deny-list (лучше allow-list `.html/.js/.css/.svg/.png/.woff*`); CSP `style-src 'unsafe-inline'` (hardening).

---

## Что сделано хорошо (сохранить)

**pnl:** SQLi нет (100% ORM + bound `text()`); argon2id; сессии — 256-бит токен,
в БД только SHA-256, ротация на логине, инвалидация на логауте/смене пароля,
HttpOnly+Secure; покрытие авторизацией всех роутов + tenant-scope; IDOR-фиксы V1/V2
на месте; Fernet для PlanFact-ключей, hard-fail без SECRET_KEY, секреты не логируются;
SSO не доверяет client-supplied sub, `dodois_sub` UNIQUE; формульный движок без eval
(ast + whitelist); xlsx 10 МБ cap + data_only; полный набор security-заголовков на
уровне приложения; CORS-middleware нет (правильно для cookie-auth дашборда);
rate-limit логина (IP+username).

**sa:** DEV_AUTH выкл на проде (`/dev/login`→404); `/internal` и `/admin` не доступны
без префикса `/api` и требуют токен (проверено снаружи → 401); PKCE S256 + state
корректны; SQLi нет; `DEFAULT_CAPABILITIES` пуст (немапленная лицензия = ничего);
лицензии создаются/правятся только под admin-токеном; нет inbound-webhook для подделки.

**front+infra:** Postgres(5432)/Redis(6379)/app(8000) НЕ опубликованы на хост (только
docker-сеть); UFW active default-deny, открыты только 22/80/443; фронт-экранирование
(`esc()`) применено ко всем внешним данным включая drill-down free-text; admin/SA на
React (auto-escape), `dangerouslySetInnerHTML`/`eval` нет; секретов в JS нет; ключи и
`.env` в gitignore; старый VPS-мост убран; `pnl-service/.env` = 600.

---

## Рекомендованный порядок устранения

1. **C1** — 1 строка (`require_admin_for_user`) — до онбординга второго админа/тенанта.
2. **H1** — `chmod 600 ~/dodotool-sa/.env` сейчас + ротация `SA_INTERNAL_TOKEN`/OAuth secret.
3. **H3** — `hmac.compare_digest` (1 строка).
4. **M1** (ssh no-password), **M2** (Caddy security-заголовки), **M3** (`/dev/*` из публичного роутинга), **L1** (`/api/internal/*` закрыть наружу).
5. **H2** — шифрование Dodo-токенов at-rest.
6. **M5/M6** — allow-list сабдоменов/origin'ов.
7. **M4** — fail-closed enforcement + гейт на xlsx/operations.
8. Low-список — по мере хардненинга.
