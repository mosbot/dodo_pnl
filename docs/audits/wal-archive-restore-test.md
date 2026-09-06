# WAL-архив Postgres → Yandex Object Storage — развёрнуто и проверено 2026-09-06

**Что работает.** Контейнер `dodotool-sa-postgres-1` на образе
`dodotool-postgres-walg:16` (`~/dodotool-sa/Dockerfile.postgres`: postgres:16 +
wal-g v3.0.9), `archive_mode=on`, `archive_command='wal-g wal-push %p'`,
`archive_timeout=300`, `wal_compression=on`. Креды бакета — только в
`/home/ask/ops/walg.env` (root:600, `env_file` у сервиса postgres).
Бакет `dodotool-pg-backups`, префикс `pg/`, endpoint
`storage.yandexcloud.net`, сервисный аккаунт `pg-backup`.

**Cron** `45 3 * * *` → `~/ops/walg-backup.sh`: `wal-g backup-push`,
`wal-g delete retain FULL 14` (WARN, пока у роли нет delete), проверка
`pg_stat_archiver` (алерт в TG при failed_count>0 или отставании >30 мин).
Лог `~/backups/walg.log`. Суточные и 4-часовые дампы остаются вторым слоем.

**Тест восстановления** (`~/ops/walg-restore-test.sh`, ~1 мин, безопасно —
отдельный контейнер `pg-restore-test` и volume, удаляются в конце):
`wal-g backup-fetch LATEST` → `recovery.signal` +
`restore_command='wal-g wal-fetch %f %p'` → старт → replay WAL до конца архива
→ promote. Результат 2026-09-06 08:34 UTC: `operations` 169/169,
`pnl_service.users` 24/24, пробные строки, записанные ПОСЛЕ базового бэкапа,
приехали через WAL (4/4). RPO ≤ 5 мин (archive_timeout) — фактически
секунды при активной записи.

**Грабли.** (1) `psql -c "insert …; select pg_switch_wal()"` в одной строке —
один неявный tx, commit уезжает в следующий сегмент: пробу писать
отдельными `-c`. (2) `docker run --network none` для `backup-fetch` — нет
доступа к бакету, висит молча. (3) Роль `storage.uploader`+`viewer` не даёт
DELETE → ретенция wal-g не работает, нужна `storage.editor` (запрошено у
Андрея); страховка — lifecycle-правило бакета 30 дней (не настроено).

**Восстановление на новый хост** (PITR): образ + `walg.env`, затем
`wal-g backup-fetch /var/lib/postgresql/data LATEST`, в
`postgresql.auto.conf`: `restore_command='wal-g wal-fetch %f %p'`,
опционально `recovery_target_time='2026-09-06 10:00:00+03'` и
`recovery_target_action=promote`, `touch recovery.signal`, старт postgres.
