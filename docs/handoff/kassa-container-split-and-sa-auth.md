# Касса: разнести контейнеры от SA + использовать SA для авторизации

Инструкция для Claude Code, работающего в репозитории **dodotool-kassa**
(`~/dodotool-kassa` на VPS `94.26.246.138`). Подготовлена из агентом pnl/Финансы
после диагностики инцидента на проде. Все факты ниже — проверены вживую
23.06.2026.

> Контекст платформы: VPS `94.26.246.138`, общая docker-сеть
> `dodotool-sa_default`, reverse-proxy — Caddy sa (`~/dodotool-sa/Caddyfile`).
> Сервисы: `dodotool-sa-api-1` (ядро auth/identity/tenancy/licensing,
> `sa.dodotool.ru`), `dodotool-pnl-api-1` (Финансы/Пульс, `pnl.dodotool.ru`),
> `dodotool-kassa-api-1` (Касса), хаб `app.dodotool.ru` (статик + проксирует
> `/api/*` на sa).

---

## ЧАСТЬ A. Разнести контейнеры (срочно — корень инцидента)

### Что сломалось

Касса после выноса в свой стек назвала docker-compose-сервис **`api`**
(`~/dodotool-kassa/docker-compose.prod.yml`, project `dodotool-kassa`). Docker
Compose **автоматически** добавляет имя сервиса как сетевой алиас. В итоге в
сети `dodotool-sa_default` алиас **`api` указывает на ДВА контейнера**:

```
dodotool-sa-api-1     172.18.0.4   aliases=[dodotool-sa-api-1, api]
dodotool-kassa-api-1  172.18.0.8   aliases=[dodotool-kassa-api-1, api, kassa-api]
```

`api:8000` резолвится в оба IP → **round-robin DNS** → ~половина запросов к
«sa» попадает в кассу, та на неизвестный путь отдаёт свой SPA `index.html`
(HTTP 200, тело `<!doctype html>…`). Потребители, парсящие ответ как JSON,
падают с `Expecting value: line 1 column 1 (char 0)`.

Кого задело: токен-брокер sa для pnl (Пульс ложился через раз), и оба
Caddy-vhost'а, проксирующие `api:8000` — `sa.dodotool.ru/api/*` и хаб
`app.dodotool.ru/api/*` (их `/me` и `/entitlements` тоже плавали).

Временный обход уже сделан агентом pnl: pnl переключён с `api` на однозначное
имя `dodotool-sa-api-1`. **Корень (алиас `api` у кассы) надо убрать здесь.**

### Фикс (в репо кассы)

1. В `docker-compose.prod.yml` переименовать сервис `api` → **`kassa-api`**.
   - Поправить `depends_on` (сервис `migrate` и т.п.), команды в комментариях
     (`up -d api` → `up -d kassa-api`), любые внутренние ссылки.
   - Сетевой алиас оставить только `kassa-api` (после переименования сервиса
     авто-алиас станет `kassa-api`; дублирующий explicit `kassa-api` можно
     убрать или оставить — он совпадает).
   - Если где-то закреплён `container_name`, имя контейнера сохранится; если
     нет — он станет `dodotool-kassa-kassa-api-1`. Проверь, что на новое имя
     никто не ссылается (Caddy-vhost кассы, healthcheck, скрипты).
2. Пересоздать стек кассы:
   ```bash
   cd ~/dodotool-kassa && sudo docker compose -f docker-compose.prod.yml up -d --force-recreate
   ```
3. Если у кассы есть свой Caddy-vhost, проксирующий на `api:8000` —
   переключить на `kassa-api:8000`.

### Рекомендация (чисто, на будущее)

Никто не должен ходить на «голый» `api`. Дать sa-api **явный устойчивый алиас**
`sa-api` (в `~/dodotool-sa/docker-compose*.yml`, networks → aliases) и перевести
ВСЕХ потребителей на `sa-api:8000`:
- `~/dodotool-sa/Caddyfile`: оба `reverse_proxy api:8000` (vhost'ы
  `sa.dodotool.ru` и `app.dodotool.ru`) → `reverse_proxy sa-api:8000`, затем
  `docker exec dodotool-sa-caddy-1 caddy reload --config /etc/caddy/Caddyfile`.
- pnl `.env`: `SA_TOKEN_BROKER_URL`/`SA_BASE_URL` сейчас на `dodotool-sa-api-1`
  — можно привести к `sa-api` (необязательно).
> Это изменения в репозиториях **dodotool-sa** и **dodo_pnl**, не в кассе.
> Делать только с владельцем. Минимально достаточно шага A1–A2 (касса
> перестаёт занимать `api` → `api` снова резолвится только в sa-api).

### Проверка (acceptance)

```bash
# api резолвится РОВНО в один IP (sa-api):
sudo docker exec dodotool-pnl-api-1 python -c "import socket;print(sorted(set(socket.gethostbyname_ex('api')[2])))"
# kassa-api резолвится в кассу:
sudo docker exec dodotool-pnl-api-1 python -c "import socket;print(socket.gethostbyname('kassa-api'))"
# токен-брокер: 10/10 JSON (а не HTML):
# (см. часть B — тот же вызов)
```

---

## ЧАСТЬ B. Использовать SA для авторизации в кассе

Модель платформы: **аутентификация и tenancy живут в SA**. Касса — потребитель.
Зеркалит то, что уже сделано в pnl (репо `mosbot/dodo_pnl`): см. эталон
`app/auth/sso.py`, `app/auth/tokens.py`, роут `GET /auth/sso`, кнопку «Войти
через Dodo IS» на `/login`.

### Три контракта SA, которые нужны кассе

База (внутри docker-сети): `SA_BASE_URL = http://dodotool-sa-api-1:8000`
(после части A можно `http://sa-api:8000`). **Никогда не используй `api:8000`.**

1. **SSO-сессия (кто пользователь).** Сессионная кука общая для `*.dodotool.ru`
   (sa выставляет её с `SESSION_DOMAIN=.dodotool.ru`). Касса должна быть на
   поддомене `*.dodotool.ru`, чтобы куку видеть.
   - На запрос без своей сессии: **проброси входящий заголовок `Cookie`** в
     `GET {SA_BASE_URL}/me`.
     - `200` → `{"sub","name","units_count"}` — пользователь известен.
     - `401` → редирект браузера на
       `https://sa.dodotool.ru/dodois/login?return_to=<публичный URL кассы>`
       (return_to allowlist'ится sa: только https на `*.dodotool.ru`).
2. **Лицензии (что доступно).** `GET {SA_BASE_URL}/entitlements` с тем же
   проброшенным `Cookie` → `{"units":[{"dodois_uuid","capabilities":[…],
   "expires_at"}]}`. Гейтить разделы по capability **`kassa`** на нужных юнитах;
   где нет — рисовать «Подключить». (Capabilities платформы: `finance`,
   `pulse`, `kassa`.)
3. **Dodo IS access-токен (если касса ходит в Dodo API).**
   `GET {SA_BASE_URL}/internal/dodois-token?sub=<sub>` с заголовком
   `X-Admin-Token: <SA_INTERNAL_TOKEN>` → `{"access_token","sub"}`.
   sa сам тихо рефрешит по offline_access. Коды: `404` — для sub нет учётных
   данных (не логинился через OAuth sa); `502` — рефреш к Dodo не удался.

> Важно (на этом и погорели): **не делай слепой `json.loads`** ответа брокера/
> sa. Проверяй `status_code` и что тело начинается с `{` (или Content-Type
> `application/json`); иначе — внятная ошибка, не «Expecting value». И ходи
> только на однозначный хост из части A.

### Конфиг кассы (`~/dodotool-kassa/.env`)

```
SA_BASE_URL=http://dodotool-sa-api-1:8000      # внутренний, не api:8000
SA_INTERNAL_TOKEN=<= ADMIN_API_TOKEN сервиса sa>   # секрет, не коммитить
SA_LOGIN_URL=https://sa.dodotool.ru/dodois/login
PUBLIC_BASE_URL=https://<kassa>.dodotool.ru    # для return_to
```
`SA_INTERNAL_TOKEN` обязан совпадать с `ADMIN_API_TOKEN` в `~/dodotool-sa/.env`.

### Нюансы

- Провижн новых аккаунтов: в sa флаг `sso_auto_provision` (default FALSE) —
  незнакомый `sub` при SSO доступа не получает. Для кассы реши политику:
  admin-managed (как в pnl: локальный аккаунт + привязка `dodois_sub`) или
  авто-провижн при реальной подписке `kassa`.
- Бесшовная миграция существующих юзеров кассы: привязать их `dodois_sub` к
  существующим записям, чтобы SSO резолвил в тот же аккаунт, а не плодил дубли
  (как сделали для PiX в pnl).

### Проверка (acceptance)

```bash
# 1) брокер: 14/14 JSON (а не HTML) — с правильного хоста:
sudo docker exec dodotool-kassa-api-1 python -c "
import os,httpx,concurrent.futures as cf
base=os.environ['SA_BASE_URL']+'/internal/dodois-token'; tok=os.environ['SA_INTERNAL_TOKEN']
def call(i):
    r=httpx.get(base,params={'sub':'<тестовый sub>'},headers={'X-Admin-Token':tok},timeout=20)
    return (r.status_code, 'JSON' if r.text.strip().startswith('{') else 'HTML')
from collections import Counter; print(Counter(call(i) for i in range(14)))
"
# 2) /me с проброшенной кукой даёт 200 при живой sa-сессии, 401 без неё.
# 3) /entitlements отдаёт capability 'kassa' для лицензированных юнитов.
```

---

## Порядок и откат

1. Часть A1–A2 (переименовать сервис кассы, пересоздать) — снимает инцидент с
   `api`-алиасом. Откат: вернуть имя сервиса, `up -d --force-recreate`.
2. (Опц., с владельцем) Часть A «рекомендация» — `sa-api` алиас + Caddy.
3. Часть B — интеграция кассы с sa-auth, постепенно (сначала `/me`+редирект,
   потом `/entitlements`-гейт, потом токен-брокер если нужен Dodo API).
