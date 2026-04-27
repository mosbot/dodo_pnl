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


def verify_password(password: str, password_hash: str) -> bool:
    """Проверить пароль против хеша. Возвращает True/False, не бросает."""
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
