#!/usr/bin/env bash
# Атомарный деплой pnl-service на проде (запускать НА VPS из корня репо).
#
# Делает: фиксирует текущее состояние коммитом → push в GitHub (deploy key)
# → pip install (только если менялся requirements.txt) → alembic upgrade head
# → рестарт сервиса → health-check. При провале health-check — АВТООТКАТ
# кода на предыдущий коммит и повторный рестарт.
#
# Использование:
#   ./scripts/deploy.sh "feat: краткое описание"
#   ./scripts/deploy.sh            # сообщение по умолчанию с датой
#
# ВНИМАНИЕ: автооткат возвращает только КОД (git reset). Если деплой
# содержал миграцию БД, она остаётся применённой — откат БД делается
# вручную (alembic downgrade) после анализа. Большинство деплоев без
# миграций откатываются полностью чисто.
set -euo pipefail

REPO="/home/claude/pnl-service"
SERVICE="pnl-uvicorn"
HEALTH_URL="http://127.0.0.1:5759/api/health"
# origin указывает на git@github-dodo:... (SSH alias с deploy key,
# см. ~/.ssh/config на VPS).
GIT_REMOTE="origin"
BRANCH="main"

cd "$REPO"

MSG="${1:-deploy $(date '+%Y-%m-%d %H:%M')}"
PREV="$(git rev-parse HEAD)"
echo "→ предыдущий HEAD: $(git rev-parse --short HEAD)"

# 1. Фиксируем изменения коммитом (если есть что фиксировать).
git add -A
if git diff --cached --quiet; then
  echo "→ рабочая копия без изменений — деплоим текущий HEAD"
else
  git commit -m "$MSG"
  echo "→ закоммичено: $(git rev-parse --short HEAD) — $MSG"
fi

# 2. Push в GitHub (журнал деплоев + бэкап истории). Не валит деплой,
#    если push не прошёл (например, нет сети до GitHub).
if git push "$GIT_REMOTE" "$BRANCH" 2>&1; then
  echo "→ запушено в $GIT_REMOTE/$BRANCH"
else
  echo "⚠ push не прошёл — продолжаю деплой (код уже закоммичен локально)"
fi

# 3. Зависимости — только если requirements.txt изменился относительно PREV.
if ! git diff --quiet "$PREV" HEAD -- requirements.txt 2>/dev/null; then
  echo "→ requirements.txt изменился — pip install"
  .venv/bin/pip install -q -r requirements.txt
fi

# 4. Миграции БД.
echo "→ alembic upgrade head"
.venv/bin/alembic upgrade head

# 5. Рестарт сервиса.
echo "→ рестарт $SERVICE"
sudo systemctl restart "$SERVICE"
sleep 4

# 6. Health-check + автооткат.
if curl -fsS -m 10 "$HEALTH_URL" >/dev/null 2>&1; then
  echo "✓ ДЕПЛОЙ OK — $(git rev-parse --short HEAD)"
else
  echo "✗ HEALTH-CHECK FAILED — откатываю код на $PREV"
  git reset --hard "$PREV"
  sudo systemctl restart "$SERVICE"
  sleep 4
  if curl -fsS -m 10 "$HEALTH_URL" >/dev/null 2>&1; then
    echo "↩ откат успешен, сервис жив на $(git rev-parse --short HEAD)"
  else
    echo "‼ откат НЕ помог — требуется ручной разбор (journalctl -u $SERVICE)"
  fi
  exit 1
fi
