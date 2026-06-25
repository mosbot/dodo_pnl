# Касса: список заведений — из SA, а не из локальной БД

Инструкция для Claude Code в репозитории **dodotool-kassa**. Самостоятельная;
опирается на auth-механику из `kassa-container-split-and-sa-auth.md` (SSO-кука,
внутренний хост sa). Факты сверены с кодом sa 23.06.2026.

## Проблема

Касса берёт список «заведений» из своего `/api/projects` (локальная таблица
кассы). В платформенной модели **источник истины по заведениям — SA**: какие
юниты доступны пользователю, определяется его ролями в Dodo IS, а их sa
заводит и хранит сам. Локальная копия в кассе будет расходиться (новые точки,
отзыв доступа, чужие юниты).

## Источник в SA (готов, ничего доделывать в sa не нужно)

`GET {SA_BASE_URL}/projects` — user-scoped список заведений текущего
пользователя.
- Защищён сессией (`CurrentUserDep`) — нужна та же SSO-кука `dt_session`, что и
  для `/me` (домен `.dodotool.ru`).
- Уже отфильтрован по юнитам, где у пользователя есть роль в Dodo IS.
- Строки sa создаёт/обновляет на каждом OAuth-логине (`_ensure_projects` в
  колбэке) — данные актуальны.
- Ответ — `list[ProjectOut]`, поля: `id`, `dodois_uuid`, `franchisee_id`,
  `title` (и доп. поля). Имя точки = `title`; ключ для связи с локальными
  данными кассы = `dodois_uuid`.

Лицензии: `GET {SA_BASE_URL}/entitlements` (та же кука) →
`{"units":[{"dodois_uuid","capabilities":[…],"expires_at"}]}`. Показывать/
активировать заведения, где в `capabilities` есть **`kassa`**; остальным —
«Подключить».

## Хост и путь (на этом легко споткнуться)

- Внутри docker-сети — **без** `/api`:
  `http://dodotool-sa-api-1:8000/projects` и `…/entitlements`.
  (Никогда не `api:8000` — алиас двоится с sa, см. auth-инструкцию.)
- Снаружи через Caddy `sa.dodotool.ru` префикс `/api` срезается, поэтому
  публично это `https://sa.dodotool.ru/api/projects` → внутри `/projects`. То
  есть «касса брала из `/api/projects`» — это и был sa через Caddy; теперь
  просто ходи на sa напрямую внутренним хостом.

## Что сделать в кассе

1. Перестать использовать локальную таблицу projects как **источник списка и
   видимости** заведений.
2. Список заведений тянуть из `GET {SA_BASE_URL}/projects`, **пробрасывая
   входящий заголовок `Cookie`** (как для `/me`). Маппить `dodois_uuid`+`title`.
3. Гейтить по `GET {SA_BASE_URL}/entitlements`: показывать только юниты с
   capability `kassa` (или помечать остальные «Подключить»).
4. Локальные данные кассы (журнал, операции) хранить по ключу `dodois_uuid`,
   но **список/доступ** всегда из sa.
5. Не делать слепой `json.loads` — проверяй `status_code`/Content-Type
   (round-robin/SPA-ответы кассы уже один раз так ломали потребителей).

## Конфиг (тот же, что для auth)

```
SA_BASE_URL=http://dodotool-sa-api-1:8000     # внутренний; не api:8000
# SSO-кука dt_session приходит от браузера (домен .dodotool.ru) — её пробрасываем
```

## Проверка (acceptance)

```bash
# с живой SSO-кукой пользователя:
#   GET {SA_BASE_URL}/projects        -> 200, список с dodois_uuid+title
#   GET {SA_BASE_URL}/entitlements    -> юниты с capabilities, есть 'kassa' у лицензированных
# без куки -> 401 (значит гейт работает)
# из контейнера кассы хост резолвится в ОДИН ip (sa-api):
sudo docker exec dodotool-kassa-api-1 python -c "import socket;print(socket.gethostbyname('dodotool-sa-api-1'))"
```

## Связанные документы

- `kassa-container-split-and-sa-auth.md` — разнесение контейнеров (алиас `api`)
  и базовая auth-интеграция кассы с sa (SSO `/me`, `/entitlements`, токен-брокер).
