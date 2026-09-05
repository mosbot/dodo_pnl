# Аудит безопасности Dodotool — 2026-09-03

Объём: код `pnl-service` (Финансы/Пульс/Планёрка) и `dodotool_sa_backend`
(SSO, брокер токенов, лицензии), плюс проверка прод-конфигурации VPS
`94.26.246.138` (Caddy, Docker, sshd, Redis, Grafana, права файлов). Касса
не аудировалась (отдельный репозиторий, не в области доступа).
Метод: ручной разбор кода двумя параллельными ревью + внешние пробы `curl`
и проверки на хосте (только чтение). Предыдущий аудит — 2026-06-22; его
незакрытые пункты учтены и пересчитаны.

## Сводка

Активно эксплуатируемых «дыр без аутентификации» нет: `DEV_AUTH` выключен,
`/dev/login` → 404, внутренние ручки требуют токен, SQL параметризован,
XSS экранирован, OAuth state/PKCE корректны. Но есть **четыре находки
уровня High**, каждая из которых при одном скомпрометированном аккаунте или
секрете даёт полный захват платформы, и одна — DoS на вход в pnl силами
анонима. Все четыре чинятся кодом за один день.

| ID | Сервис | Серьёзность | Суть | Статус на проде |
|---|---|---|---|---|
| P1 | pnl | **High** | network_admin может сбросить пароль / понизить / удалить super_admin'а своего тенанта | эксплуатируемо: в PiX (key 1) super_admin и network_admin на одном ключе |
| P2 | pnl | **High** | uvicorn без `--proxy-headers`: все клиенты = IP Caddy → 5 неверных паролей блокируют вход **всем** на 15 мин; per-IP лимит не работает | подтверждено (`Cmd` без флага, `FORWARDED_ALLOW_IPS` не задан) |
| S1 | sa | **High** | Брокер токенов `/api/internal/dodois-token` доступен из интернета; принимает `ADMIN_PASSWORD` (пароль оператора) наравне с internal-токеном; без rate-limit | подтверждено: снаружи 401 (защита только токеном) |
| S2 | sa | **High** | Онбординг принимает `franchisee_id` из тела: чужой юнит можно вписать в чужую сеть или «сквоттить» сеть жертвы по её sub → стать держателем подписки | код; на проде не воспроизводилось |
| S3 | sa | **High** | Refresh-токены Dodo IS в БД открытым текстом; единственная роль Postgres `dodotool_sa` у sa и pnl (брокер обходится напрямую) | подтверждено (одна login-роль) |
| P3 | pnl | Medium | `/auth/link?lt=` без nonce — CSRF-привязка чужого Dodo IS sub (sa-кука SameSite=Lax → GET-навигация несёт её) → login-CSRF и чужой Dodo-токен через брокер | подтверждено: `dt_session` = Lax |
| P4 | pnl | Medium | `POST /api/ops-metrics/sync` и write/delete ops-metrics доступны любому пользователю (visibility 10): удаление frozen-снапшотов + тяжёлые синки по любому месяцу | код |
| P5 | pnl | Medium | `/api/pnl` без верхней границы диапазона дат → fan-out запросов к Dodo IS (квота 200 rpm токена сети) | код |
| P6 | pnl | Medium | `dodo_unit_uuid` в конфиге проекта не сверяется с юнитами тенанта → обход лицензии по чужому UUID, перехват онбординга чужой сети через access-request | код |
| S4 | sa | Medium | Кука `dt_session` на всём `.dodotool.ru` уходит в Grafana (анонимный Viewer публично), stg и др.; любой поддомен может её перезаписать; сессия stateless — logout не ревокует | подтверждено: Grafana anonymous=Viewer, 200 снаружи |
| S5 | sa/infra | Medium | Redis без пароля в общей docker-сети pnl/касса/monitor/grafana | подтверждено (`requirepass` пуст) |
| S6 | sa | Medium | Один секрет `ADMIN_API_TOKEN` на операторов, pnl, backup-хост; аудита вызовов нет | код |
| I1 | infra | Medium | sshd `PasswordAuthentication yes` (пункт M1 прошлого аудита, не закрыт) | подтверждено |
| I2 | infra | Medium | `~/pnl-staging/.env.staging` с прод-`SA_INTERNAL_TOKEN` — права 664 | подтверждено |
| I3 | infra | Medium | Контейнеры sa и pnl работают от root; `COPY . .` в образе sa | подтверждено (uid 0) |
| S7 | sa | Low | Нет security-заголовков на sa/app/kassa vhost'ах (у pnl есть) | подтверждено |
| S8/P7 | оба | Low | `/api/docs` (sa) и `/docs` (pnl) открыты в проде | подтверждено (200) |
| P8 | pnl | Low | Open redirect `/auth/sso?next=/\evil.com` (backslash не отфильтрован) | код |
| P9 | pnl | Low | Тайминг-enumeration логинов (argon2 не вызывается для несуществующего) | код |
| P10 | pnl | Low | Внутренние URL/ошибки Dodo IS в текстах 400/502 | код |
| P11 | pnl | Low | Планёрка: `anchor` без валидации (500), `project_ids` без hidden-list | код |
| P12 | pnl | Low | Rolling-TTL сессий 30 дн без абсолютного максимума; `SettingIn` без лимита длины | код |
| P13 | pnl | Low | `fastapi 0.115.4`/starlette <0.47.2 (CVE-2025-54121, multipart), `cryptography 43.0.1` | requirements.txt |
| S9 | sa | Low | CORS `*`, GET-логаут, `/users/names` перечисляет ФИО любых sub, `_is_safe_next` пускает любой поддомен, лишние зависимости (authlib, flower, starsessions, aiocache) | код |
| I4 | infra | Low | MCP слушает `0.0.0.0:8788` (закрыт ufw до 172.18/16) — лучше bind на 172.18.0.1 | подтверждено |

Закрыто с прошлого аудита: C1 (кросс-тенантный сброс пароля), H3 (`compare_digest`), M4 ч.1 (enforcement), права `.env` прод (600). Не закрыто: H1-остаток (ротация OAuth client_secret), H2 (=S3), M1 (=I1), M2 (=S7), M3 (`/dev/*` в Caddy — сейчас безвреден при DEV_AUTH=false, но маршрут остался), M4 ч.2 (fail-closed enforcement), M5/M6 (=S4/S9), L1 (=S1), L2, L3, L4 (=P6), L5.

## Детали ключевых находок

### P1 — вертикальная эскалация в pnl
`require_admin_for_user` (`app/auth/dependencies.py:125-150`) сверяет только
`planfact_key_id` цели и актора, не роль цели. Network_admin тенанта, где
есть super_admin (сейчас — PiX), может `POST /api/admin/users/{id}/reset-password`
и получить новый пароль super_admin'а в ответе → полный контроль над всеми
тенантами, ключами PlanFact, аудитом. Также `PATCH` роли и `DELETE`.
**Фикс:** в фабрике и трёх ручках — `target.is_super_admin and not
actor.is_super_admin → 403`; запрет менять роль тому, чья роль выше своей.

### P2 — proxy-headers
`Dockerfile:21`: `uvicorn app.main:app --host 0.0.0.0 --port 8000` без
`--proxy-headers --forwarded-allow-ips`. `request.client.host` для всех
= IP Caddy. `login_limiter` per-IP (5/15 мин) срабатывает на весь сервис:
аноним пятью неверными паролями отключает вход всем пользователям на
15 минут; одновременно per-IP лимит не различает атакующих (остаётся только
per-username 15/15). IP в `sessions`/`audit_log` бесполезны.
**Фикс:** `CMD uvicorn … --proxy-headers --forwarded-allow-ips="*"`
(контейнер не экспонирован, только Caddy) — то же в sa.

### S1 — брокер токенов из интернета под паролем админки
Caddy для `sa.dodotool.ru`/`app.dodotool.ru` пробрасывает `/api/*` целиком,
включая `/internal/*`. `require_admin_token` принимает **либо**
`ADMIN_API_TOKEN`, **либо** `ADMIN_PASSWORD` (`app/admin_auth.py:19-30`) —
пароль, который оператор вводит в SPA. Rate-limit нет. Подбор/фишинг
пароля = Dodo IS access-токены всех клиентов (продажи, персонал, ПДн).
**Фикс:** Caddy `@internal path /api/internal/*` → `respond 404`; отдельный
`INTERNAL_TOKEN` для `/internal/*`, `ADMIN_PASSWORD` там не принимать;
rate-limit на 401; лог выдачи токена (sub, caller).

### S2 — cross-tenant через онбординг
`app/routers/onboarding.py:44-66` проверяет роль по `dodois_uuid`, а
`franchisee_id`/имя/business берёт из тела; `onboard_unit` переиспользует
существующую сеть без проверки владельца, отсутствующую создаёт с любым id.
Сценарии: вписать свой юнит в чужую сеть (стать держателем подписки, если
своя подписка старше — Касса гейтит визард по этому флагу); «сквоттинг»
сети по sub жертвы до её первого входа (`_ensure_franchisee` использует
`franchisee_id = user.sub`).
**Фикс:** `franchisee_id` от клиента не принимать: сеть = своя
(`owner_sub == user.sub`) или 403; id новой сети генерирует сервер; тесты.

### S3 — токены открытым текстом, одна БД-роль
`DodoisCredentials.access_token/refresh_token` — `Text` без шифрования;
refresh с `offline_access` долгоживущие: дамп/бэкап = постоянный доступ ко
всем аккаунтам. pnl подключается той же ролью `dodotool_sa` → может читать
`dodotool_sa.dodois_credentials` напрямую, обесценивая брокер.
**Фикс:** Fernet/AES-GCM для refresh-токенов (ключ в env, как `SECRET_KEY`
у pnl); отдельная роль `pnl_service` с правами только на свою БД; шифровать
бэкапы.

### P3 — CSRF-привязка Dodo IS sub
`make_link_token(user_id)` — Fernet с user_id и 10-минутным TTL, без nonce
и привязки к браузеру. Атакующий берёт свой `lt` из `/auth/link/start` и
подсовывает жертве `GET /auth/link?lt=…`; `dt_session` (Lax) уходит с
навигацией → sub жертвы пишется в аккаунт атакующего. Далее login-CSRF и,
если у тенанта атакующего нет владельца с sub, — Dodo-токен жертвы через
брокер.
**Фикс:** nonce в короткоживущей cookie `pnl_link_state` + в payload
токена; завершать привязку POST'ом под `pnl_session` со страницей
подтверждения.

## План исправлений

### Волна 0 — ВЫПОЛНЕНО 2026-09-03 (pnl 6072a32; sa — правки в VPS-чекауте ~/dodotool-sa: main.py, Dockerfile, Caddyfile; бэкап Caddyfile.bak-audit-*)

Проверено снаружи: /api/internal/*, /dev/login, /api/docs, /internal/* → 404 на sa/app/pnl/stg; изнутри docker-сети pnl→sa брокер 200, sa→pnl activity 200, MCP→loopback 200; в логах uvicorn реальные IP клиентов.

1. **P1** — гейт роли цели в `require_admin_for_user` + три ручки. Тест:
   network_admin → reset-password super_admin → 403.
2. **P2** — `--proxy-headers --forwarded-allow-ips="*"` в Dockerfile pnl и
   sa; проверить, что Caddy ставит `X-Forwarded-For`. Тест: `sessions.ip`
   = реальный IP.
3. **S1 (часть)** — Caddy: `/api/internal/*` → 404 на всех vhost'ах;
   `/dev/*` убрать из маршрутов sa. Проверка: снаружи 404, pnl→sa по
   docker-сети 200, MCP через loopback 200.
4. **I2** — `chmod 600 ~/pnl-staging/.env.staging`.
5. **S8/P7** — `docs_url=None, redoc_url=None, openapi_url=None` в проде
   обоих сервисов.
6. **P8** — фильтр `\` и управляющих символов в `next` (`urlsplit(next).netloc == ""`).

### Волна 1 — ВЫПОЛНЕНО 2026-09-03 (кроме S5 Redis — бриф Кассе; Grafana анон — решение владельца «пока оставить»)

Сделано: S2 (онбординг без клиентского franchisee_id, +4 теста, 125 тестов sa зелёные), P3 (nonce-cookie `pnl_link_state` для /auth/link), S1/S6 (`INTERNAL_TOKENS` по вызывающему: pnl/mcp/backup, лог выдачи токенов, `ADMIN_PASSWORD` для /internal → 401; общий `ADMIN_API_TOKEN` временно как `shared-admin` до переезда Кассы, выключается `INTERNAL_STRICT=true`), P4 (ops-metrics write/delete → territorial; sync: окно ≤24 мес, не будущее, закрытые месяцы → territorial+), P5 (`_validate_date_range` ≤750 дн на /api/pnl и /api/operations, limit ≤1000), P6 (`_assert_unit_belongs_to_tenant` по /auth/roles/units владельца, super_admin без ограничения), I1 (sshd: PasswordAuthentication no, PermitRootLogin no — `00-hardening.conf`, т.к. cloud-init 50-… стоит раньше и побеждает первое вхождение), S7 (сниппет `security_headers` на всех vhost'ах Caddy), deploy.sh — push после health-check. pnl e6b6eed.

### Волна 1 — исходный план

7. **S2** — онбординг без клиентского `franchisee_id`; тесты на чужую
   сеть и сквоттинг.
8. **P3** — nonce + POST-подтверждение привязки.
9. **S1 (часть 2) / S6** — отдельный `INTERNAL_TOKEN` (pnl, MCP, backup —
   каждому свой), `ADMIN_PASSWORD` только для SPA-гейта; лог `/admin/*` и
   `/internal/*` (sub, caller, без значений токенов); rate-limit на 401.
10. **P4** — `require_territorial` на `/api/ops-metrics/sync` и write/delete
    ops-metrics; `period` в окне ≤24 мес и не в будущем; удаление
    frozen-снапшота — только админу.
11. **P5** — валидация `date_start/date_end` (ISO, порядок, ≤24 мес, не
    будущее), `limit ≤ 1000` в `/api/operations`.
12. **P6** — при записи `dodo_unit_uuid` сверять с `fetch_units` владельца
    тенанта (кэш `dodois_units_cache` уже есть).
13. **I1** — sshd `PasswordAuthentication no` (ключи у обоих пользователей
    есть), `PermitRootLogin no`.
14. **S5** — Redis `requirepass` + пароль в `REDIS_URL` sa и кассы (касса
    делит db0 — согласовать с их сессией, одновременный рестарт).
15. **S4 (часть)** — Grafana: выключить анонимный доступ или Caddy basic-auth;
    это же убирает утечку `dt_session` в незащищённый сервис.
16. **S7** — security-заголовки на sa/app/kassa vhost'ах (HSTS, nosniff,
    X-Frame-Options, Referrer-Policy) — как у pnl.

### Волна 2 — ВЫПОЛНЕНО 2026-09-03 (вечер)

S3: токены Dodo IS шифруются Fernet в БД (`app/crypto.py` EncryptedText,
`CREDENTIALS_ENC_KEY` в sa .env — НЕ ТЕРЯТЬ, иначе всем повторный вход;
бэкфилл `scripts/encrypt_credentials.py`, 14/14 строк зашифрованы);
Postgres-роли: `pnl_service` (владелец БД pnl_service, нет CONNECT к
dodotool_sa — проверено FATAL), `kassa_prod`/`kassa_stg` заведены (реквизиты
для Кассы в ~/ops/notify.env), с БД снят PUBLIC CONNECT. S4: сессии sa в
Redis (starsessions, `SESSION_STORE=redis`, TTL 24 ч rolling, кука — только
sid), ревокация `app/sessions_index.py` (logout гасит все сессии sub;
RefreshRejected в celery — тоже), `_is_safe_next` по allow-list хостов
(`RETURN_TO_HOSTS`), CORS по allow-list вместо `*`. I3: оба образа non-root
(uid 10001), sa `COPY` явным списком. M4ч2: enforcement fail-closed —
last-known-good caps при сбое sa, сбой не кэшируется. P13: fastapi 0.116.1,
uvicorn 0.35, cryptography 44.0.1. Хвосты: P9 constant-time login, P10
generic-тексты ошибок, P11 валидация anchor + hidden-projects в Планёрке,
P12 абсолютный TTL сессии 90 дн и лимиты длины настроек, S9 логаут
GET+POST и `/users/names` только по своим сетям, I4 MCP слушает
172.18.0.1 и секретный путь 192 бита (коннектор claude.ai нужно
переподключить на новый URL), уборка старых `.env.bak*`.

Прод после волны 2: pnl eb4c640, sa — VPS-чекаут; тесты sa 127 passed;
health всех доменов 200.

### Волна 2 — исходный план

17. **S3** — шифрование refresh-токенов (миграция: зашифровать существующие
    на месте), отдельная Postgres-роль для pnl, шифрованные бэкапы,
    ротация OAuth `client_secret` (H1-остаток; ручное действие в кабинете
    Dodo IS + `.env` sa + re-consent).
18. **S4 (часть 2)** — серверный session-store с ревокацией
    (`starsessions` уже в зависимостях), `max_age` ≤ 24 ч, инвалидация
    сессий sub при `RefreshRejected`; список поддоменов для куки/`return_to`
    (`app`, `kassa`, `pnl`, `sa`, `stg*`) вместо «любой `*.dodotool.ru`».
19. **I3** — non-root `USER` в обоих Dockerfile, явный `COPY` вместо `COPY . .`.
20. **M4 ч.2** — enforcement лицензий fail-closed: не кэшировать None-сбой
    sa, падать на last-known-good caps.
21. **P13** — `fastapi ≥0.116`/`starlette ≥0.47.2`, `cryptography ≥44.0.1`;
    `pip-audit`/`uv run pip-audit` в CI обоих репо.
22. **S9, P9–P12, I4** — мелочи: CORS allow-list, POST-логаут, `/users/names`
    по своим сетям, dummy-verify argon2, generic-тексты ошибок, валидация
    `anchor`, абсолютный TTL сессии 90 дн, `max_length` настроек, bind MCP на
    172.18.0.1; удалить неиспользуемые зависимости sa.

### Тесты, которых нет (добавить вместе с фиксами)

pnl: эскалация роли (P1), link-CSRF (P3), лимиты дат (P5), чужой UUID (P6),
open-redirect варианты (`/\`, `//`, `/\\`).
sa: онбординг в чужую `franchisee_id` и сквоттинг (S2), `/internal/*` с
`ADMIN_PASSWORD` → 401, `/entitlements` без проектов, callback с `?error=`,
инвайт-флоу целиком, holder при юните из двух сетей, отсутствие `/dev/login`
при `DEV_AUTH=false`, флаги куки.

### Что проверено и в порядке

Тенантная изоляция данных pnl (все data-ручки скоупятся по сессии,
`project_ids` пересекаются с allowed-set); сессии pnl (32 байта, SHA-256 в
БД, HttpOnly/Secure/Strict, ревокация при смене пароля, Argon2id); CSRF на
state-changing ручках (JSON + Strict/Lax); SQL параметризован везде;
статика (`SafeStaticFiles`, guard .py, `.env` вне образа); секреты не в
логах и не в ответах (PlanFact-ключ маскируется); XSS (esc() везде, CSP
`script-src 'self'` у pnl); XLSX-инъекция закрыта; OAuth state/PKCE
одноразовые; `_is_safe_next` устойчив к userinfo/суффиксным атакам;
инвайты одноразовые; Celery JSON-сериализация; Postgres не экспонирован;
`compare_digest` в admin-auth; sa-зависимости свежие (fastapi 0.136,
starlette 1.0, httpx 0.28, cryptography 48).

### Инцидент 2026-09-03 «Invalid OAuth state» при входе в Кассу — ЗАКРЫТ

Причина — в Кассе: после переезда сборки фронта в Actions в `deploy.yml`
попал `VITE_OAUTH_LOGIN_URL=/dodois/login`, 401-обработчик SPA стал вести
на legacy-роут Кассы, который генерил PKCE-state в своей сессии, а колбэк
принимал sa (`DODOIS_REDIRECT_URI` → `sa.dodotool.ru/dodois/callback`).
К переезду сессий sa в Redis отношения не имело.

Хронология: костыль sa — `redir /dodois/login → sa /dodois/login?return_to=…`
в Caddy для kassa/stg-kassa; Касса удалила `/dodois/login`+`/dodois/callback`
(фронт `171eb83`, бэк `608dc51`), все редиректы на вход абсолютные на SA,
`next` только относительный, 503 при ненастроенном SA, регресс-тесты.
Костыль снят (Caddy restart, бэкап `Caddyfile.bak-oauthfix-remove-*`):
`/dodois/login` → 404 на обоих контурах, `/auth/sso` → 302 на sa, вход
проверен в приватном окне. Побочный фикс в sa остаётся: повторный колбэк у
залогиненного пользователя — редирект в приложение, а не 400.
Хвост у Кассы: `DODOIS_CLIENT_ID/SECRET` ещё читает legacy `token_refresh.py`
— вычистить вместе с ним. **`INTERNAL_STRICT=true` включён 2026-09-03 вечером**
(Касса prod/stg уже ходят со своими токенами; общий `ADMIN_API_TOKEN` на
`/internal/*` → 401, проверено).

### Что осталось после трёх волн (на 2026-09-03, вечер)

1. Ротация OAuth `client_secret` cnM4i (кабинет Dodo IS → `.env` sa →
   re-consent) — ручное, у Андрея.
2. Коммит sa-изменений: VPS-чекаут `~/dodotool-sa` содержит все правки волн
   0–2 (55 файлов), в репо `dodotool_sa_backend` не запушены. pnl — в main.
3. `pip-audit` в CI обоих репо (P13 часть 2).
4. Тесты pnl из списка выше (P1/P3/P5/P6/open-redirect) — не написаны;
   sa-тесты по S1/S2 есть.
5. Grafana anonymous Viewer — «пока оставить» (владелец).
6. Zero-downtime деплой pnl (секунды 502 при рестарте) — требование
   маркетплейса, не безопасность.

## Дополнение 2026-09-05 — по открытым вопросам Кассы

- **Redis ACL** (вместо общего `requirepass`): пользователи `sa`, `kassa`,
  `kassa-stg` (`~* &* +@all`, префиксы ключей — после ответа Кассы),
  `default off`. ACL-файл `~/dodotool-sa/redis/users.acl` (gitignore), compose
  redis `--aclfile /data/users.acl`, volume `./redis:/data`, healthcheck под
  `sa`. `REDIS_URL` вида `redis://<user>:<pw>@redis:6379` в `.env` sa, kassa,
  kassa-staging; бэкапы `*.bak-acl-<ts>`. Проверено: старый пароль →
  WRONGPASS, 25 ключей сохранены, все контейнеры healthy, kassa `/auth/sso` 302.
- **Дампы каждые 4 часа**: `~/ops/backup-db-intraday.sh` (dodotool_sa +
  pnl_service, `pg_dump -Fc`, проверка `pg_restore --list`, ретенция 48 ч в
  `~/backups/intraday`), cron `0 7,11,15,19,23 * * *`. RPO ≤4 ч; WAL-архив —
  ждёт решения по хосту.
- **Refresh-задача sa** пропускает учётки с `expires_at` старше 14 дней
  (`STALE_AFTER_DAYS`, `app/token_refresh.py`, sa `d977f29`) — 9 отозванных
  учёток больше не держат задачу «красной».
- Попутно: **celery-beat sa крашился с 03.09** (non-root uid 10001 не имел
  прав на volume `/var/celerybeat`) — `chown -R 10001:10001`, beat запущен.
  Последствие волны 2 (non-root образы); проверять volume-права при смене uid.
- Решения владельца: держатель XFood не переоформляется; запрос scope
  `marketplace` в Dodo IS отправлен; Grafana anonymous — пока оставить.
- sa-репо синхронизирован с VPS-чекаутом (`314861f`, `d977f29`).
