# Инцидент: leak backend исходников через `/static/`

**Дата обнаружения:** 2026-06-03.
**Период утечки:** 2026-05-08 13:42 → 2026-06-03 ~14:35 MSK (≈26 дней).
**Severity:** Medium (бизнес-логика и архитектура утекли; credentials не утекли — они в `.env`).

## Что произошло

В `pnl-service/static/` лежали копии backend исходников:

| Файл | Размер | Содержимое |
| ---- | ------ | ---------- |
| `main.py` | 73 КБ, 1811 строк | FastAPI endpoints, auth, business logic |
| `pnl.py` | 74 КБ, 1514 строк | Расчёт P&L строк, формулы UC/LC/DC/EBITDA |
| `models.py` | 17 КБ, 395 строк | SQLAlchemy схема БД |
| `schemas.py` | 5 КБ | Pydantic-схемы API контракта |
| `storage.py` | 32 КБ, 1:1 с актуальным `app/storage.py` | SQL queries, persistence layer |

FastAPI монтировал `/static/` через дефолтный `StaticFiles(directory=...)`,
который раздаёт ЛЮБОЙ файл из директории. Caddy ничего не фильтровал —
простой `reverse_proxy localhost:5759`.

В итоге файлы были доступны по `https://pnl.dodotool.ru/static/main.py`
с HTTP 200 для любого посетителя.

## Что НЕ утекло

- `.env` (DB password, encryption secret) — не лежал в `static/`.
- PlanFact API ключи — хранятся в БД зашифрованными AES-GCM, в исходниках их нет.
- JWT secret — в `.env`.
- Хеши паролей юзеров — в БД.
- TLS приватный ключ — у Caddy.

## Что утекло

- **Бизнес-логика** расчёта P&L (наша «изюминка»): формулы UC/LC/DC,
  template_path_to_code, классификатор категорий, маппинг проектов.
- **Схема БД**: все таблицы, индексы, связи.
- **API-контракт**: endpoints, параметры, response schemas.
- **Архитектура auth**: роли SA/NA/User, scope filtering, dependency-фабрики.
- **Известные баги/обходы**: комментарии S11.1 (parallel-split `/operations`),
  cache_history логика, target normalization edge-cases — всё с обоснованиями.

## Как попали туда

Точно неизвестно. Файлы все имеют идентичный mtime `2026-05-08 13:42`,
что указывает на одну операцию (batch copy). На 8 мая у нас была активная
работа над Phase 1-4 ролей (commits 9a015f3, 1ea6a76 и т.д.). Гипотезы:

- Ошибка деплоя: команда `cp app/*.py static/` вместо `cp app/*.py …`.
- Ручное копирование «чтобы посмотреть в браузере без auth» и забыли.
- Скрипт миграции/импорта, который писал бэкапы не туда.

bash_history на VPS не сохранён достаточно глубоко, чтобы восстановить точно.

## Логи

**Эксплуатация неизвестна.** Caddy для `pnl.dodotool.ru` блока не имел
директивы `log` — все запросы за 26 дней утеряны. Невозможно сказать,
сколько раз эти URL были запрошены и кем.

## Что сделано (2026-06-03)

### 1. Удалены файлы
`rm /home/claude/pnl-service/static/{main,pnl,models,schemas,storage}.py`.
Проверено: `GET /static/main.py → 404`.

### 2. Защита уровня приложения — `SafeStaticFiles`

В `app/main.py` дефолтный `StaticFiles` заменён на кастомный
`SafeStaticFiles(StaticFiles)` с deny-листом по расширению:

```python
DENIED_EXTENSIONS = frozenset({
    ".py", ".pyc", ".pyo", ".pyd",
    ".env", ".envrc",
    ".key", ".pem", ".crt", ".p12",
    ".db", ".sqlite", ".sqlite3",
    ".sh", ".bash",
    ".yml", ".yaml",
    ".toml", ".ini", ".cfg",
    ".log", ".sql",
})
```

Любой запрос на файл с deny-расширением → 404 (намеренно, не 403 — чтобы
не подтверждать существование).

### 3. Защита уровня фронт-прокси — Caddy

В `pnl.dodotool.ru` блок добавлен:

```
@static_sensitive {
    path /static/*.py /static/*.pyc /static/*.env* /static/*.key /static/*.pem
    path /static/*.sql /static/*.db /static/*.sqlite* /static/*.yml /static/*.yaml
}
respond @static_sensitive 404
```

Запросы блокируются до того, как доходят до приложения. Двойная защита:
если когда-то SafeStaticFiles случайно уберут, Caddy всё равно отрубит.

### 4. Access-логи Caddy

Добавлено логирование public-запросов в `/home/fintool/caddy_pnl_access.log`,
JSON-формат, ротация 100 МБ × 10 файлов. На будущее: видеть кто что
дёргает на pnl-домене (раньше pnl-блок логов вообще не имел).

### 5. .gitignore

Добавлены правила, чтобы случайный `git add static/*.py` не прошёл:

```
static/*.py
static/*.pyc
static/*.env
static/*.key
static/*.pem
static/*.sql
static/*.db
static/*.sqlite
static/*.sqlite3
static/__pycache__/
```

## Verification

```
$ curl -sS -o /dev/null -w "%{http_code}" https://pnl.dodotool.ru/static/main.py
404
$ curl -sS -o /dev/null -w "%{http_code}" https://pnl.dodotool.ru/static/random.env
404
$ curl -sS -o /dev/null -w "%{http_code}" https://pnl.dodotool.ru/static/../../etc/passwd
404
$ curl -sS -o /dev/null -w "%{http_code}" https://pnl.dodotool.ru/static/app.js
200
```

## Открытые вопросы

- **Стоит ли публично disclose (PlanFact/Dodo IS/клиентам)?** На мой взгляд
  нет: ничьи данные не утекли, только наш собственный код. Кодовая база сейчас
  всё равно в публичном GitHub (`mosbot/dodo_pnl`) — то что утекло, и так
  доступно через `git clone`. Архитектурная разведка теоретически облегчает
  атаку, но без credentials всё равно нужны другие уязвимости.
- **Стоит ли подписать GitHub репо приватным?** Сейчас он public. Это вопрос
  отдельный, не блокирующий.
- **Cron / CI чек:** можно добавить простой shell-чек в CI/deploy:
  `find static/ -name "*.py" -exec false {} +` — упасть если найдены.
  Сейчас защита есть в коде и в Caddy, CI-чек был бы четвёртый уровень.

## Lessons

1. **Дефолтный `StaticFiles` опасен.** В любом проекте нужно либо
   ограничивать deny-листом, либо хранить только assets в отдельной
   директории `static/assets/` с whitelist по расширению.
2. **Все public-домены должны иметь access-логи.** Без них инцидент
   невозможно реконструировать.
3. **mtime — единственный надёжный timestamp** в инцидентах такого рода,
   если bash_history короткий. Sudo + auditd были бы лучше, но overkill.
