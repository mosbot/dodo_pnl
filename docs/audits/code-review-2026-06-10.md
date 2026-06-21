# Комплексный code review + план исправлений — 2026-06-10

Охват: все `app/*.py`, `app/auth/*.py`, вся `static/`, все md-доки (CLAUDE.md, README, audits, dodois-api, planfact-agent-kit). Проверено вручную: auth-слой, crypto, admin_router; ключевые находки верифицированы по коду.

**Общий вердикт.** Меж-тенантная изоляция (`planfact_key_id`) выдержана последовательно — дыр «сеть видит чужую сеть» не найдено. Auth-слой сильный: argon2id, серверные сессии в HttpOnly+Secure+SameSite=strict cookie, rate-limit логина, audit log, эскалация ролей в admin_router закрыта. SQL-инъекций нет, формульный парсер — корректный AST-whitelist. Основные проблемы — **внутри-тенантная** авторизация (роль 10 видит данные ролей 60–100), несколько функциональных багов и UI-долги на /board.

---

## 1. Уязвимости

### HIGH

**V1. Обход видимости проектов через явный `project_ids` (IDOR внутри тенанта)**
`app/main.py:708` — `_resolve_project_filter` возвращает явно переданный `project_ids` без пересечения с активными проектами ключа и hidden-list юзера. Управляющий (visibility 10) вызывает `GET /api/pnl?project_ids=<чужая_точка>` и получает полный P&L любого проекта своей сети, включая скрытые. Затрагивает `/api/pnl`, `/api/pnl.xlsx`, `/api/revenue-history`, `/api/board` (там пересечение с конфигом ключа есть, но hidden-list игнорируется).

**V2. `/api/operations` и `/api/operations.xlsx` — без фильтрации по видимости**
`app/main.py:1088, 1193` — принимают произвольные `project_ids`/`category_ids`, тянут операции из PlanFact напрямую. Юзер 10-го уровня делает drill-down по категориям DIVIDENDS/TAX/MGMT и видит суммы, контрагентов, комментарии — это обнуляет всю фильтрацию `_filter_lines_by_visibility` в pnl.py.

**V3. Rate-limiter и audit видят `127.0.0.1` вместо реального IP — НЕ ПОДТВЕРДИЛОСЬ (проверено на проде 2026-06-10)**
В коде обработки `X-Forwarded-For` нет и в systemd-unit флага `--proxy-headers` нет, но у современного uvicorn `proxy_headers=True` по умолчанию с `forwarded-allow-ips=127.0.0.1` — nginx ходит с localhost, и в логах видны реальные клиентские IP. Rate-limiter и audit работают корректно. Остаточная рекомендация: прописать `--proxy-headers --forwarded-allow-ips=127.0.0.1` в unit явно, чтобы поведение не зависело от дефолтов версии uvicorn.

### MEDIUM

**V4. `category_breakdown` / `revenue_by_channel` не фильтруются по visibility_level**
`app/pnl.py:800–813, 983–999` — `lines` фильтруются, а breakdown отдаётся целиком с `pnl_code` (DIVIDENDS, INTEREST, TAX) и суммами. Скрытые строки восстанавливаются из ответа `/api/pnl` через DevTools. Комментарий «никогда не видит закрытых строк даже через DevTools» (pnl.py:849) не соответствует коду.

**V5. Formula injection в xlsx-экспортах**
`app/xlsx_export.py:270, 308–312` (и titles на 137, 206) — `label` (query-параметр) и `comment` (из PlanFact) пишутся в ячейки as-is. Строка, начинающаяся с `=`/`+`/`-`/`@`, интерпретируется Excel как формула (`=HYPERLINK`, DDE `=cmd|...`).

**V6. Stored XSS через label метрик (admin → users своей сети)**
`static/app.js:933, 987, 1032, 2103` — `label` в `pctTile`/`opsTile`/`finTile`/`renderAggregateTable` вставляется в innerHTML без `esc()`. Label редактируется network_admin'ом через настройки → stored XSS у всех юзеров сети. Cookie не украсть (HttpOnly), но действия от имени юзера — да. В остальных ~95% мест `esc()` применяется корректно.

**V7. CSP-bypass через `cdn.jsdelivr.net` + нет SRI**
`static/index.html:8`, `app/main.py:57` — CSP разрешает весь jsdelivr (любой пакет оттуда — готовый гаджет при любой найденной XSS), Chart.js грузится без `integrity=`. Self-host (один файл, статика и так версионируется `?v=N`) или SRI + сузить CSP.

**V8. Session-токены в БД открытым текстом**
`app/auth/sessions.py` — в `user_sessions.token` лежит сырой токен. Read-доступ к БД (бэкап, SQL-инъекция в соседнем сервисе на том же Postgres) = угон всех сессий. Хранить SHA-256(token), сравнивать по хэшу.

**V9. Тихий no-op fallback шифрования**
`app/crypto.py:65–74` — пустой `SECRET_KEY` → PlanFact-ключи пишутся в БД открытым текстом, только warning в логах. На проде должно быть fail-hard (отказ старта или отказ записи секретов).

### LOW

- **V10.** `/api/health` (`main.py:369`) без авторизации отдаёт RSS, потоки, число PF-клиентов и кэшей. Урезать до `{"status":"ok"}` для анонимов.
- **V11.** Мокапы `static/*-mock.html` публичны без сессии и содержат реальные имена точек (Кубинка-1/2, Одинцово-3, Голицыно-1) и правдоподобные выручки. Убрать из `static/`.
- **V12.** Временный пароль `AdmTest2026!` для `andrey@dodotool.ru` закоммичен в CLAUDE.md. Сменить пароль (если ещё не), убрать из репо.
- **V13.** `period_month`/`metric_code` не валидируются по формату (мусор в PK). Regex `^(\d{4}-\d{2}|__default__)$`.
- **V14.** Rate-limit логина только per-IP, per-username нет — распределённый brute-force одного аккаунта не ограничен.

---

## 2. Функциональные баги

**B1. Дубль `delete_cache_entry` — инвалидация закрытого месяца не коммитится**
`app/store.py:1105` и `:1157` — вторая функция (возвращает `None`) затирает первую (возвращает `bool`). В `main.py:1710` `if snapshot_invalidated:` всегда False → `commit()` не выполняется. Кнопка «Обновить» для закрытых месяцев не работает. Удалить дубль на 1157.

**B2. /board: краш при пустом выборе проектов**
`static/board.js:395, 904` — снял все тумблеры → `TypeError ... reading 'value'` → красный бар «Ошибка» вместо empty-state.

**B3. /board: нет обработки 401 и нет race-guard**
`board.js:137, 343` — протухшая сессия → «Ошибка: 401» вместо редиректа на login; каждый клик тумблера = немедленный полный `/api/board` (десятки Dodo IS-запросов) без debounce и loadCounter → лишняя нагрузка (при вашей истории rate-limit-инцидентов!) и stale-рендер. Плюс рассинхрон: пустой выбор в `reloadBoardData` уходит как запрос **без** `project_ids` = весь ключ.

**B4. `_MAX_PARALLEL=6` не используется**
`app/dodois_client.py:32` — константа мёртвая, реальный параллелизм ничем не ограничен кроме структуры gather в board.py. CLAUDE.md утверждает, что лимит действует — это неправда. С учётом rate-limit-инцидентов — вернуть семафор реально.

**B5. `asyncio.get_event_loop()` в fire-and-forget close**
`app/planfact.py:415, 437, 452` — deprecated/бросает без running loop, исключение глушится → утечка httpx-коннектов (тот самый OOM-вектор); таски не сохраняются в ссылки (GC). `get_running_loop()` + хранить ссылки.

**B6. `_BOARD_CACHE` — race + неограниченный рост в пределах часа**
`main.py:1584, 1640–1666` — без lock'а, ключ включает filter_key → дублирование тяжёлых сборок (известная задача #3) и рост словаря. `asyncio.Lock` + кэш на (key, hour) с фильтрацией на отдаче.

**B7. Мелкие**: discard несовпадающего кортежа в `_OPS_SYNC_INFLIGHT` (main.py:1750); новый httpx-клиент на каждый Dodo IS-вызов (~15+ TLS-хендшейков на /api/board); двойной `esc()` в crit-summary (board.js:613 — `&amp;` в тексте); дубликат логики `_normalize_uuid` в ops-sync.

---

## 3. План исправлений (по фазам)

### Фаза 0 — hotfix ✅ ВЫПОЛНЕНО И ЗАДЕПЛОЕНО 2026-06-10
1. **V1**: в `_resolve_project_filter` пересекать явный `project_ids` с `(active − hidden)`; прогнать через него `/api/board`.
2. **V2**: те же проверки в `/api/operations{,.xlsx}` + отклонять `category_ids`, маппящиеся на коды с `min_visibility_level > user.visibility_level`.
3. **B1**: удалить дубль `delete_cache_entry` (store.py:1157).
4. **V6**: обернуть 4 label'а в `esc()` (app.js) — 5 минут.
5. **B2**: guard `p.totals?.day` + empty-state на /board.
6. **V12**: сменить/убрать пароль из CLAUDE.md.

### Фаза 1 — эта неделя ✅ ВЫПОЛНЕНО И ЗАДЕПЛОЕНО 2026-06-10
7. **V3**: uvicorn `--proxy-headers --forwarded-allow-ips=127.0.0.1` (правка systemd-unit) — почини и rate-limit, и audit-IP. Проверить, что nginx шлёт `X-Forwarded-For`.
8. **V4**: фильтровать `category_breakdown`/`revenue_by_channel` тем же min-level маппингом, что и lines.
9. **V5**: экранирование опасных первых символов при записи текста в xlsx (общий helper `_safe_cell`).
10. **V7**: self-host Chart.js, убрать jsdelivr из CSP.
11. **V10, V11**: health → `{"status":"ok"}` без auth; мокапы из `static/` в `docs/mockups/`.
12. **B3**: 401-redirect + debounce 500–800 мс + loadId-guard в board.js.

### Фаза 2 — стабильность ✅ ВЫПОЛНЕНО И ЗАДЕПЛОЕНО 2026-06-10 (миграция 0024)
13. **B4**: реальный `asyncio.Semaphore(_MAX_PARALLEL)` в dodois_client + переиспользуемый httpx-клиент (по образцу PlanFactClient).
14. **B5**: `get_running_loop()` + set ссылок на таски в planfact.py.
15. **B6**: lock + LRU для `_BOARD_CACHE` (заодно закрывает задачу #3 из бэклога).
16. **V8**: хранить SHA-256 от session-токена (миграция: колонка token_hash, переходный период по prefix).
17. **V9**: fail-hard при пустом SECRET_KEY вне dev-режима.
18. **V13, V14, B7**: валидация period_month, per-username limiter, мелочи.

### Фаза 3 — гигиена + UI ✅ ВЫПОЛНЕНО И ЗАДЕПЛОЕНО 2026-06-10
19. ✅ Мёртвый код удалён: `app/storage.py` целиком, `fetch_productivity`, `TARGETABLE_METRICS`, `fmtPct`/`signClass`/`previousMonthKey` (app.js), `REFRESH_MS`/`MSK_OFFSET_H`/`refreshTimer`/`rank`/`.card-grid`-CSS ~100 строк (board).
20. ⏳ ОТЛОЖЕНО: вынести `shared.js` (esc/fmt/drawer/selection — продублированы board.js ↔ app.js) и `board.css` из inline — рефакторинг без функциональных изменений, делать отдельным заходом.

UI-правки Фазы 3 (задеплоено): тач-таргеты строк drawer ≥44px + строка-label целиком кликабельна (P0); кнопка ⟳ «Обновить» в topbar /board + amber-счётчик после 5 мин (P1); @media ≤640px для topbar /board (P1); контраст мелких цифр --muted-2→--muted (P1); aria-live на toast, role=alert на boardError, aria-label сортировки (P2); 0% теперь нейтральный в fmtDelta; убрана двойная стрелка «↓ ↕».

Осталось из UX-рекомендаций (некритично): focus-trap в drawer/модалках, сортировка чипами вместо цикл-кнопки, прогноз в rich-card по порогу, табы settings на 360px, alert()→toast (2 места), per-user boardView/boardSort.

---

## 4. Оценка UI и рекомендации

### Сильные стороны (сохранить)
- /board: иерархия hero → карточки, нейтрализация шума |Δ|≤3%, crit/warn-рамки, `tabular-nums`, защита от tel:-автолинков — продуманный мобильный сценарий.
- Empty-state на index (3 шага с прогрессом) и loading-скелетоны, повторяющие реальную структуру, — уровень выше среднего.
- Drill-down со сверкой суммы против P&L (`mismatch`) — доверие к цифрам.
- Sticky-колонка + line-clamp в мобильной таблице — видно итерации по реальной боли.

### Рекомендации по приоритету

**P0**
- Тач-таргеты drawer'а: `.switch` 28×16 px и строка `.proj-row` ~24 px при норме 44 px — главный интерактив выбора точек. Сделать всю строку кликабельной + padding 10px на мобиле.
- Empty-state /board вместо краша (см. B2).

**P1**
- Выбор проектов на /board: либо паттерн «Применить/Сбросить» как на index, либо debounce + спиннер. Сейчас 5 кликов = 5 тяжёлых рефетчей и мигание.
- Кнопка «Обновить» в topbar /board рядом со счётчиком «обн. N мин» (auto-refresh отключён осознанно, но «перезагрузите страницу» в футере — слабый affordance на телефоне); окрашивать счётчик в amber после 5+ мин.
- Контраст: `--muted-2 #a3a39e` на белом ≈ 2.4:1 при 10px («vs 184 000») — не проходит WCAG AA (нужно 4.5:1). Для мелких цифр использовать минимум `--muted #6e6e6a`.
- Topbar /board без единого `@media` — на 360–390px не влезает. Скопировать стратегию index (@600px: скрыть «· PlanFact», имя юзера, ужать «⚙ Настройки» до иконки).

**P2**
- Доступность: `role="status"`/`aria-live` на toast, `role="alert"` на #boardError; focus-trap и возврат фокуса в drawer/модалках; `overflow:hidden` на body под модалкой.
- Сортировка-циклическая кнопка (4 состояния прокликом) → чипы или select; убрать двойную стрелку «↓ ↕».
- Rich-card: прогноз в футере каждой карточки дублирует hero и добавляет ~30 строк скролла на сеть — показывать только при |Δ| > порога.
- settings: 6 табов без горизонтального скролла на 360px; `alert()` вместо toast в двух местах.

**P3**
- `fmtDelta`: 0% красится зелёным в hero (в `fmtDeltaShort` уже neutral — рассинхрон).
- per-user ключи localStorage: ждать `/auth/me` без таймаута 800 мс (иначе преференсы юзеров мешаются через `default`); `boardView`/`boardSort` сделать per-user.

---

## 5. Сводная таблица

| ID | Находка | Серьёзность | Фаза |
|----|---------|-------------|------|
| V1 | IDOR через project_ids | High | 0 |
| V2 | /api/operations без visibility | High | 0 |
| V3 | Глобальный lockout (proxy IP) | Не подтвердилось | — |
| V4 | category_breakdown утечка | Medium | 1 |
| V5 | xlsx formula injection | Medium | 1 |
| V6 | Stored XSS через label метрик | Medium | 0 |
| V7 | CSP/jsdelivr без SRI | Medium | 1 |
| V8 | Сырые session-токены в БД | Medium | 2 |
| V9 | Тихий no-op crypto fallback | Medium | 2 |
| V10–V14 | health/мокапы/пароль/валидация/brute-force | Low | 1–2 |
| B1 | Дубль delete_cache_entry → «Обновить» сломан | High (функц.) | 0 |
| B2 | Краш /board при пустом выборе | High (функц.) | 0 |
| B3 | 401/debounce на /board | Medium | 1 |
| B4 | _MAX_PARALLEL не работает | Medium | 2 |
| B5 | get_event_loop утечка коннектов | Medium | 2 |
| B6 | _BOARD_CACHE race/рост | Medium | 2 |
