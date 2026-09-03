"""Argon2id хеширование паролей.

Используем argon2-cffi с профилем по умолчанию (Argon2id, time_cost=2,
memory_cost=64 MiB, parallelism=8). Эти параметры — рекомендация OWASP 2023
для интерактивной аутентификации; выдают ~50ms на современном CPU, что
делает brute-force нерентабельным.

Хеши self-describing — внутри лежат соль, параметры и алгоритм-id, поэтому
функция verify() не требует отдельной соли и переживает смену параметров
без миграций (PasswordHasher.check_needs_rehash подскажет, когда пора).
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError


_ph = PasswordHasher()


def hash_password(password: str) -> str:
    """Сгенерировать argon2id хеш пароля. Соль внутри хеша, отдельно не хранить."""
    return _ph.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Проверить пароль против хеша. Возвращает True/False, не бросает.

    password_hash может быть None/пустым у SSO-юзеров (вход через Dodo IS, без
    локального пароля) — такой локальный логин всегда отклоняем."""
    if not password_hash:
        # Аудит 2026-09-03 P9: тратим то же время, что и на реальную проверку —
        # иначе по задержке отличается «нет такого логина» от «неверный пароль».
        _dummy_verify()
        return False
    try:
        _ph.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True если хеш использует устаревшие параметры (после смены _ph настроек).
    На login-handler-е если ok+needs_rehash → перехешить и обновить в БД."""
    try:
        return _ph.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# Хеш-заглушка для постоянного времени ответа (P9). Считается один раз.
_DUMMY_HASH = _ph.hash("dummy-password-for-constant-time")


def _dummy_verify() -> None:
    try:
        _ph.verify(_DUMMY_HASH, "wrong")
    except Exception:  # noqa: BLE001 — ожидаемо не совпадает
        pass
