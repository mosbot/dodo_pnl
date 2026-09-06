# WAL-архив Postgres → Yandex Object Storage (runbook)

Цель: RPO ≤ 5 мин для БД `dodotool_sa` (sa + Касса) и `pnl_service`, восстановление
на любой момент времени (PITR), копии вне VPS. Инструмент — `wal-g`
(S3-совместимый API Object Storage). Стоимость ≈ 50 ₽/мес
(см. `yandex-cloud-migration.md`).

## Часть 1 — что нужно от Андрея (кабинет Yandex Cloud, ~20 мин)

1. **Аккаунт и биллинг**: https://console.yandex.cloud → войти Яндекс ID →
   создать платёжный аккаунт (юрлицо ИП или физлицо, привязать карту).
   Free-tier грант, если предложат, — принять.
2. **Каталог**: облако по умолчанию → каталог (folder), например `dodotool`.
3. **Бакет** (Object Storage → Создать бакет):
   - имя: `dodotool-pg-backups` (глобально уникальное, если занято —
     добавить суффикс);
   - доступ: **ограниченный** (никакого публичного чтения);
   - класс хранилища: **Стандартное** (холодное дороже на операциях);
   - версионирование: выключено; шифрование: можно включить KMS-ключом
     по умолчанию — не обязательно;
   - жизненный цикл: правило «удалять объекты старше 30 дней» с префиксом
     `pg/` (ретенцию wal-g ведёт сам, это страховка).
4. **Сервисный аккаунт** (IAM → Сервисные аккаунты → Создать):
   - имя `pg-backup`;
   - роль **`storage.uploader`** на бакет (не на каталог): в бакете →
     «Права доступа» → назначить роль `storage.uploader` сервисному
     аккаунту. Если UI не даёт на уровне бакета — `storage.editor` на
     каталог, но тогда в бакете больше ничего не хранить.
   - Для восстановления понадобится ещё `storage.viewer` — можно выдать
     сразу.
5. **Статический ключ доступа**: сервисный аккаунт → «Создать новый ключ»
   → «Статический ключ доступа». Показывается один раз: `key_id`
   (20 символов) и `secret` (40 символов).
6. **Передать мне**, НЕ через чат/мессенджер: на VPS
   `ssh ask@94.26.246.138` и записать в файл
   ```
   sudo install -m 600 -o root -g root /dev/null /home/ask/ops/walg.env
   sudo nano /home/ask/ops/walg.env
   ```
   содержимое:
   ```
   AWS_ACCESS_KEY_ID=<key_id>
   AWS_SECRET_ACCESS_KEY=<secret>
   WALG_S3_PREFIX=s3://dodotool-pg-backups/pg
   AWS_ENDPOINT=https://storage.yandexcloud.net
   AWS_REGION=ru-central1
   ```
   Затем сказать мне «ключ на месте». Второй экземпляр ключа — в свой
   менеджер паролей (для восстановления с другого хоста).

Что НЕ нужно: ВМ, Managed PostgreSQL, ALB, публичный IP — только бакет
и ключ.

## Часть 2 — что делаю я (~2 ч, окно не нужно, БД не останавливается)

1. Образ Postgres с `wal-g`: `Dockerfile.postgres` в `~/dodotool-sa`
   (`FROM postgres:16` + бинарник wal-g), сервис `postgres` в
   `docker-compose.prod.yml` переводится на этот образ; `walg.env`
   монтируется как `env_file` только в контейнер БД.
2. Конфиг Postgres (через `command`/`postgresql.conf`):
   `wal_level=replica`, `archive_mode=on`,
   `archive_command='wal-g wal-push %p'`, `archive_timeout=300`,
   `wal_compression=on`. Требует один рестарт Postgres (~5 с, ночью;
   sa/pnl/kassa переподключаются сами).
3. Базовый бэкап раз в сутки 03:30 через cron хоста:
   `docker exec dodotool-sa-postgres-1 wal-g backup-push /var/lib/postgresql/data`,
   затем `wal-g delete retain FULL 14 --confirm`. Уведомление в TG при
   ошибке — тем же `~/ops/notify.env`, что у текущих бэкапов.
4. Существующие дампы (суточные + 4-часовые) остаются как второй, независимый
   слой ещё месяц, потом 4-часовые выключаем.
5. **Проверка восстановления** — обязательная: `wal-g backup-fetch` +
   `wal-g wal-fetch` в отдельный контейнер `pg-restore-test` с
   `recovery_target_time` = 10 мин назад; сверка `count(*)` по
   `operations`, `subscriptions`, `pnl_service.sessions`. Результат и
   команды — в `docs/audits/`.
6. Мониторинг: `wal-g backup-list` в ежедневном watchdog; алерт, если
   последний бэкап старше 26 ч или `pg_stat_archiver.failed_count` растёт.

## Восстановление (кратко, для памяти)

```
docker run --rm -it --env-file /home/ask/ops/walg.env -v pgrestore:/var/lib/postgresql/data postgres-walg \
  wal-g backup-fetch /var/lib/postgresql/data LATEST
# recovery.signal + restore_command='wal-g wal-fetch %f %p' + recovery_target_time='...'
```
Полная процедура появится в `docs/audits/wal-archive-restore-test.md` после
шага 5.
