#!/usr/bin/env bash
# Синхронизирует docs/planfact-agent-kit/ с upstream github.com/planfact/planfact-agent-kit.
# Запуск: ./_scripts/pull_planfact_docs.sh
#
# Что делает:
#   1. shallow clone репозитория в /tmp;
#   2. чистит локальный docs/planfact-agent-kit/ (кроме .source);
#   3. копирует все .md + LICENSE из репо;
#   4. пишет в .source SHA коммита, дату и URL — чтобы было видно, на какой версии стоим.
#
# Зачем не submodule: pnl-service сам git-репо, вложенный .git создаёт
# submodule-like состояние, которое путает CI и diff. Простой sync даёт
# материал на месте + чистый git status; цена обновления — один запуск скрипта.

set -euo pipefail

REPO="https://github.com/planfact/planfact-agent-kit.git"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/docs/planfact-agent-kit"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "→ clone $REPO (shallow)"
git clone --depth=1 --quiet "$REPO" "$TMP/repo"

SHA="$(cd "$TMP/repo" && git rev-parse HEAD)"
DATE="$(cd "$TMP/repo" && git log -1 --format=%cI)"

echo "→ rebuild $DEST"
mkdir -p "$DEST"
# Чистим всё кроме .source (на случай если в нём ручные пометки)
find "$DEST" -mindepth 1 -maxdepth 1 ! -name '.source' -exec rm -rf {} +

# Копируем .md и LICENSE — всё остальное не нужно (там только это и есть, но
# защищаемся на будущее)
shopt -s nullglob
for f in "$TMP/repo"/*.md "$TMP/repo"/LICENSE; do
  [ -f "$f" ] && cp "$f" "$DEST/"
done

cat > "$DEST/.source" <<EOF
upstream: $REPO
commit:   $SHA
date:     $DATE
synced:   $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

COUNT="$(find "$DEST" -maxdepth 1 -name '*.md' | wc -l | tr -d ' ')"
echo "✓ sync done: $COUNT .md files at commit ${SHA:0:8}"
echo "  $DEST/.source"
