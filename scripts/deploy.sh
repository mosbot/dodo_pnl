#!/usr/bin/env bash
# Деплой pnl-service на SA-VPS (Docker + Caddy). Запускать из git-чекаута на VPS:
#   ./scripts/deploy.sh "feat: описание"
#   ./scripts/deploy.sh                 # сообщение по умолчанию с датой
#
# Делает атомарно:
#   1) git: коммит локальных правок (если есть) → pull --ff-only → push origin main
#   2) docker compose build + up -d (пересборка образа pnl и перезапуск контейнера)
#   3) alembic upgrade head внутри контейнера (на неизменной БД — no-op)
#   4) healthcheck /api/health; при провале — АВТООТКАТ кода на прошлый коммит
#
# NB: автооткат возвращает только КОД. Если деплой содержал миграцию БД, она
# остаётся применённой — откат БД (alembic downgrade) делается вручную.
# Статику кэш-бастим вручную через ?v=N в html.
set -euo pipefail
cd "$(dirname "$0")/.."

MSG="${1:-deploy $(date '+%Y-%m-%d %H:%M')}"
COMPOSE="docker-compose.pnl.yml"
CONT="dodotool-pnl-api-1"
PREV="$(git rev-parse HEAD)"
echo "→ предыдущий HEAD: $(git rev-parse --short HEAD)"

# 1) git: зафиксировать локальные правки, синхронизироваться, запушить
git add -A
if git diff --cached --quiet; then
  echo "→ рабочая копия без изменений — деплоим текущий HEAD"
else
  git commit -m "$MSG"
  echo "→ закоммичено: $(git rev-parse --short HEAD) — $MSG"
fi
git pull --ff-only origin main
git push origin main && echo "→ запушено в origin/main" || echo "⚠ push не прошёл — продолжаю"

# 2) сборка образа + перезапуск контейнера
echo "→ docker compose build + up -d"
sudo docker compose -f "$COMPOSE" up -d --build

# 3) миграции БД (на неизменной БД — no-op)
echo "→ alembic upgrade head"
sudo docker exec "$CONT" alembic upgrade head

# 4) healthcheck с автооткатом кода
echo "→ healthcheck"
ok=0
for _ in $(seq 1 10); do
  if sudo docker exec "$CONT" python -c "import urllib.request as u;u.urlopen('http://localhost:8000/api/health',timeout=5)" 2>/dev/null; then
    ok=1; break
  fi
  sleep 2
done

if [ "$ok" != "1" ]; then
  echo "✗ HEALTHCHECK FAILED → откат кода на $PREV (миграции БД, если были, откатить вручную)"
  git reset --hard "$PREV"
  sudo docker compose -f "$COMPOSE" up -d --build
  exit 1
fi

echo "✓ ДЕПЛОЙ OK — $(git rev-parse --short HEAD)"
