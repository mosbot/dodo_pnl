# pnl-service (Финансы/Пульс) — контейнер для SA-VPS (Docker + Caddy).
FROM python:3.11-slim

WORKDIR /app

# Зависимости — отдельным слоем для кэша.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY static ./static
COPY scripts ./scripts

EXPOSE 8000

# Миграции применяем отдельно (alembic upgrade head) — при первом запуске БД
# уже восстановлена из дампа на текущей версии. Сервер:
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
