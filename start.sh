#!/usr/bin/env bash
# Запуск P&L Dashboard локально без Docker.
# bash start.sh  (или chmod +x start.sh и двойной клик)
set -euo pipefail
cd "$(dirname "$0")"

pick_python() {
  # Явное переопределение через переменную PYTHON имеет приоритет
  if [ -n "${PYTHON:-}" ] && command -v "$PYTHON" >/dev/null 2>&1; then
    echo "$PYTHON"; return
  fi
  # Ищем Python 3.10+ — новее в приоритете
  for cand in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then
      echo "$cand"; return
    fi
  done
  # Homebrew часто не в PATH для GUI-запуска — посмотрим напрямую
  for p in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
           /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 \
           /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
           /usr/local/bin/python3.11 /usr/local/bin/python3.10; do
    if [ -x "$p" ]; then echo "$p"; return; fi
  done
  # Последний шанс — любой python3 (может оказаться 3.9, тогда упадёт ниже)
  if command -v python3 >/dev/null 2>&1; then echo python3; return; fi
  echo ""
}

PY="$(pick_python)"
if [ -z "$PY" ]; then
  echo "❌ Python не найден. Установи Python 3.10+:"
  echo "   brew install python@3.12"
  exit 1
fi

VER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
MAJOR="${VER%.*}"; MINOR="${VER#*.}"
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]; }; then
  echo "❌ Найден Python $VER ($PY), но нужен 3.10+."
  echo "   Установи:  brew install python@3.12"
  echo "   После — удали .venv и запусти снова:  rm -rf .venv && ./start.sh"
  exit 1
fi

echo "→ Python: $PY (версия $VER)"

# Проверим, что существующий .venv собран нужной версией — иначе пересоздадим
if [ -d .venv ]; then
  VENV_VER="$(.venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")"
  if [ "$VENV_VER" != "$VER" ]; then
    echo "→ .venv собран другим Python ($VENV_VER), пересоздаю"
    rm -rf .venv
  fi
fi

if [ ! -d .venv ]; then
  echo "→ Создаю виртуальное окружение .venv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "→ Ставлю зависимости"
pip install -q --upgrade pip >/dev/null
pip install -q -r requirements.txt

mkdir -p data

PORT="${PORT:-8000}"
echo
echo "==============================================="
echo "  P&L Dashboard запускается на http://localhost:$PORT"
echo "  Остановить: Ctrl+C"
echo "==============================================="
echo
exec uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload
