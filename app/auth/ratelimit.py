"""In-process rate limiter для /auth/login.

Простая sliding-window-схема: за каждым IP держим deque попыток с timestamp.
При попытке логина — отбрасываем устаревшие, считаем оставшиеся. Если ≥ N —
HTTP 429.

Для одного uvicorn-воркера достаточно. При горизонтальном масштабировании —
заменить на Redis. Для нашего workload (10-20 юзеров) это не понадобится
ещё долго.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock


class LoginRateLimiter:
    """Sliding-window rate limit. Thread-safe (uvicorn под нагрузкой использует
    asyncio в одном процессе, но вспомогательные потоки тоже могут шалить —
    Lock не помешает)."""

    def __init__(self, max_attempts: int = 5, window_minutes: int = 15):
        self.max_attempts = max_attempts
        self.window = timedelta(minutes=window_minutes)
        self._failures: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = Lock()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def check(self, ip: str) -> tuple[bool, int]:
        """Проверить, разрешена ли попытка. Возвращает (allowed, retry_after_sec).

        Не записывает попытку — это делает record_failure после неудачного
        verify_password. Позволяет различать «слишком много неудачных» и
        «впервые попробовал, дай шанс».
        """
        now = self._now()
        cutoff = now - self.window
        with self._lock:
            q = self._failures[ip]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) < self.max_attempts:
                return True, 0
            # До истечения старшей попытки
            oldest = q[0]
            retry_after = int((oldest + self.window - now).total_seconds())
            return False, max(retry_after, 1)

    def record_failure(self, ip: str) -> None:
        """Зафиксировать неудачную попытку логина."""
        now = self._now()
        with self._lock:
            self._failures[ip].append(now)

    def reset(self, ip: str) -> None:
        """Очистить счётчик после успешного логина."""
        with self._lock:
            self._failures.pop(ip, None)


# Singleton — общий для всех login-запросов внутри одного uvicorn-воркера.
login_limiter = LoginRateLimiter(max_attempts=5, window_minutes=15)
