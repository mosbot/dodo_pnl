# Security audit — pnl-service, 2026-06-13

Полный аудит всего проекта (по запросу). Только отчёт — код не менялся.
Предыдущий аудит: `code-review-2026-06-10.md` (фиксы V1–V14 уже в проде).
Этот проход подтверждает, что прошлые фиксы на месте, и фокусируется на
поиске оставшихся рисков, включая код, добавленный после 2026-06-10
(v2-миграция S20, board-кэш S21, git-деплой, S22 Dodo-выручка).

## Резюме

Проект в хорошей форме по безопасности — видна работа прошлого ревью.
Найдена **одна значимая проблема**: межтенантная IDOR на трёх admin-
эндпоинтах (network_admin может читать/изменять/удалять данные чужого
PlanFact-ключа). Остальное — low/informational хардненинг.

| # | Уровень | Тема | Статус |
|---|---------|------|--------|
| H1 | **High** | Межтенантная IDOR: admin cache/config-эндпоинты без проверки владения ключом | **исправлено** (d518cf3) |
| M1 | Medium | Загрузка .xlsx без лимита размера + парсинг недоверенного архива | **исправлено** (d518cf3) |
| L1 | Low | CSP `style-src 'unsafe-inline'` | принято/хардненинг |
| L2 | Low | `SafeStaticFiles` использует deny-list, а не allow-list | хардненинг |
| L3 | Low | Rate-limit логина — in-process (per-worker) | ок для текущего масштаба |
| L4 | Low | Видимость проектов завязана на per-user hidden-list, не на visibility_level | дизайн-нюанс |
| I1–I5 | Info | KDF для Fernet, '=' -только guard в xlsx, отсутствие CSRF-токенов (есть SameSite), pip-audit, downgrade миграций | инфо |

## Что проверено и признано безопасным

- **SQL-инъекции** — нет. Всё через SQLAlchemy ORM (bound params),
  единственный raw SQL — статичный DDL в миграциях, без пользовательского
  ввода.
- **Парсер формул** (`formulas.py`) — нет eval/RCE. `ast.parse` только для
  разбора; дерево конвертируется в собственные узлы по строгому whitelist
  (числа, `[N]`, `+ - * /`, унарный минус); `Name`/`Call`(кроме внутр.
  `_L`)/`Attribute`/`**` отвергаются. Деление на ноль → None.
- **Аутентификация** — Argon2id (OWASP-профиль) для паролей; session-токен
  `secrets.token_hex(32)` (256 бит), в БД хранится SHA-256 (read-БД не даёт
  угнать сессию); кука `HttpOnly + Secure + SameSite=Strict`, TTL 30д,
  rolling refresh.
- **Авторизация** — каждый эндпоинт имеет явный `Depends(require_*)`; ролевая
  модель (super/network admin, visibility_level) + per-key/per-user скоуп-
  фабрики. IDOR-фиксы V1 (`_resolve_project_filter` — клиентские project_ids
  пересекаются с active−hidden) и V2 (`_authorize_operations_drilldown` —
  drilldown по статье проверяет min_visibility_level) на месте.
- **SSRF** — нет. `base_url` Dodo/PlanFact из конфига, не от пользователя;
  пути фиксированы. TLS-проверка httpx включена (`verify=False` нигде нет).
- **Секреты** — PlanFact api_key шифруется в БД (Fernet/AES-128+HMAC из
  SECRET_KEY); startup hard-fail если задан DATABASE_URL, но нет SECRET_KEY
  (V9). В логи токены/пароли/ключи не пишутся. `.gitignore` покрывает
  `.env`, `.claude-ssh/`, статику-секреты; в трекаемых файлах хардкод-
  секретов нет.
- **Web-поверхность** — CSP self-only (`script-src 'self'`, self-hosted
  Chart.js), HSTS, `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`. `SafeStaticFiles` отдаёт 404 на
  `.py/.env/.key/.pem/...` (фикс инцидента утечки исходников).
- **Formula-injection в экспорте** (V5) — `_set_text` форсит `data_type='s'`
  для строк с `=`.
- **Brute-force** — rate-limit логина по IP (5/15мин) и по username
  (15/15мин, V14).
- **/api/health** — анонимам только `{"status":"ok"}`; диагностика
  (RSS/threads/кэш) только админам (V10).
- **S22 (Dodo-выручка)** — новый код не добавляет authz-поверхности: вызов
  внутри `_build_pnl_v2_result` за `require_user` + скоуп по
  `planfact_key_id`; uuids берутся из конфига ключа (не от клиента); флаг
  серверный; ошибки Dodo → graceful fallback. Инъекций/SSRF нет.

---

## H1 — Межтенантная IDOR на admin cache/config-эндпоинтах (High)

**Где:** `app/auth/admin_router.py`
- `GET  /api/admin/planfact-keys/{key_id}/cache` (`admin_list_key_cache`, ~стр. 859)
- `DELETE /api/admin/planfact-keys/{key_id}/cache/{period_month}` (`admin_delete_key_cache`, ~стр. 889)
- `PATCH /api/admin/planfact-keys/{key_id}/live-months-window` (`admin_update_live_months_window`, ~стр. 909)

**Суть.** Эти три эндпоинта защищены `Depends(require_admin)` — т.е. «любой
администратор» — и работают с `key_id` из пути, делая `session.get(PlanfactKey,
key_id)` **без проверки, что ключ принадлежит сети админа**. В отличие от
соседних эндпоинтов (`/{key_id}/pnl-source`, `/{key_id}` PATCH/DELETE и др.),
которые корректно используют `Depends(require_admin_for_key("key_id"))`.

`require_admin` пропускает и `network_admin`, и `super_admin`. Значит
`network_admin` сети A, подставив `key_id` сети B, может:
- **прочитать** список замороженных месяцев чужого ключа + `username`, кто их
  заморозил (раскрытие данных другого тенанта);
- **удалить/переоткрыть** закрытый месяц чужого ключа (деструктив: форсирует
  пересборку снэпшота, влияет на финансовые данные другого тенанта);
- **изменить** `live_months_window` чужого ключа (порча конфигурации).

`admin_list_planfact_keys` (GET список) — для сравнения — фильтрует корректно:
`if admin.is_network_admin: stmt.where(PlanfactKey.id == admin.planfact_key_id)`.

**Эксплуатируемость сейчас.** Зависит от того, есть ли больше одного
`network_admin` на разные ключи. При текущем деплое (PiX и Xfood, фактически
один оператор) практический риск низкий. Но это латентный изъян авторизации,
который «выстреливает» ровно в момент онбординга второй сети с собственным
network_admin — то есть при штатном развитии мультитенантного SaaS. Поэтому
уровень **High** (cross-tenant read + destructive write), с оговоркой про
текущую низкую эксплуатируемость.

**Рекомендация (минимальная, паттерн уже есть в коде).** Заменить на этих трёх
эндпоинтах `Depends(require_admin)` → `Depends(require_admin_for_key("key_id"))`.
super_admin продолжит работать с любым ключом, network_admin — только со своим.
Опционально добавить регрессионный тест: network_admin сети A получает 403 на
`key_id` сети B для каждого из трёх маршрутов.

---

## M1 — Загрузка .xlsx без лимита размера (Medium)

**Где:** `POST /api/template/preview` (`app/main.py` ~стр. 2480),
`content = await file.read()` без ограничения; затем `openpyxl` парсит
недоверенный архив.

**Суть.** Файл целиком читается в память без cap'а, и .xlsx — это zip:
возможен decompression bomb / OOM. Эндпоинт под `require_admin`, поэтому
вектор ограничен администраторами → **Medium** (не аноним), но DoS одним
большим/«бомбовым» файлом реален.

**Рекомендация.** Ввести лимит размера (например, проверять
`request.headers["content-length"]` и/или читать стримом с порогом, ~5–10 МБ
для экспорта ОПУ достаточно). Опционально — распаковку считать через лимит
на суммарный размер членов архива.

---

## Low / Informational

- **L1 — CSP `style-src 'unsafe-inline'`.** Допускает инлайновые стили
  (нужны для модалок `style=...`). При запертом `script-src 'self'` риск XSS
  низкий; для полного хардненинга — вынести стили в классы и убрать
  `unsafe-inline`.
- **L2 — `SafeStaticFiles` = deny-list.** Блокирует известные опасные
  расширения. Allow-list (отдавать только `.html/.js/.css/.svg/.png/.woff*`)
  устойчивее к будущим типам. Defense-in-depth.
- **L3 — Rate-limit логина in-process.** Sliding-window в памяти воркера; при
  >1 воркера/горизонтальном масштабировании лимит обходится. Для 10–20
  юзеров и одного воркера — ок; при росте вынести в Redis (уже отмечено в
  docstring).
- **L4 — Модель видимости проектов.** Срез данных в `/api/pnl` и `/api/board`
  определяется per-user hidden-list + visibility_level статьи, но не
  visibility_level на уровне самих точек. Юзер уровня 10 без настроенного
  hidden-list увидит все точки ключа. Это осознанный дизайн; для
  defense-in-depth — дефолтить новых низкоуровневых юзеров на ограниченный
  набор точек.
- **I1 — Fernet-ключ = `SHA-256(secret_key)`** (не медленный KDF). Приемлемо,
  т.к. secret_key высокоэнтропийный (`token_hex(32)`), а не пароль.
- **I2 — xlsx guard только для `=`.** Достаточно: openpyxl превращает в
  формулу лишь ведущий `=`; `+ - @` пишутся как текст.
- **I3 — CSRF-токенов нет**, но `SameSite=Strict` + cookie-auth закрывают
  cross-site CSRF. Достаточно для текущей модели.
- **I4 — Зависимости запиннены** (хорошо для воспроизводимости); запиненные
  версии со временем накапливают CVE. Рекомендация: периодический
  `pip-audit` / Dependabot.
- **I5 — Откат миграций.** `deploy.sh` авто-откатывает только КОД, не БД
  (уже задокументировано в CLAUDE.md). Не security, но операционный риск при
  откате релиза с миграцией.

## Приоритеты

1. **H1** — заменить `require_admin` → `require_admin_for_key("key_id")` на
   трёх эндпоинтах (тривиально, паттерн в коде есть). Сделать до онбординга
   второй сети.
2. **M1** — лимит размера на загрузку .xlsx.
3. L1–L4 / I4 — по возможности, как плановый хардненинг.
