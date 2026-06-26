"""Шифрование чувствительных полей (PlanFact API ключи и т.п.) в БД.

Используется Fernet (AES-128 в режиме CBC + HMAC-SHA256). Ключ Fernet
производится из settings.secret_key через SHA-256 → base64. Один и тот
же secret_key даёт детерминированный Fernet-ключ.

Формат хранения:
    encrypted: "enc:<base64>" — Fernet token
    legacy plain: всё остальное (включая пустую строку)

Это позволяет:
1. Развернуть код без миграции БД — старые plain-ключи продолжают читаться.
2. По мере перевыпуска (admin update) поля переезжают на encrypted.
3. Если secret_key пустой → encryption выключено целиком, всё работает
   как раньше (с предупреждением в логах при старте).

Импорт: `from app.crypto import encrypt_secret, decrypt_secret`.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

log = logging.getLogger(__name__)

ENC_PREFIX = "enc:"


def _derive_fernet_key(secret_key: str) -> Optional[bytes]:
    """Из произвольной строки secret_key → 32-байтный URL-safe base64 ключ
    для Fernet. Возвращает None, если secret_key пустой."""
    if not secret_key:
        return None
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_fernet: Optional[Fernet] = None


def _get_fernet() -> Optional[Fernet]:
    """Lazy-init Fernet. Если settings.secret_key пустой — возвращаем None
    и шифрование становится no-op (предупреждение в логах при первом вызове)."""
    global _fernet
    if _fernet is not None:
        return _fernet
    key = _derive_fernet_key(settings.secret_key)
    if key is None:
        log.warning(
            "crypto: settings.secret_key пустой — шифрование чувствительных "
            "полей отключено. Сгенерируй ключ: python -c 'import secrets; "
            "print(secrets.token_hex(32))' и положи в .env как SECRET_KEY="
        )
        return None
    _fernet = Fernet(key)
    return _fernet


def encrypt_secret(value: str) -> str:
    """Зашифровать строку. Если secret_key не настроен → вернуть как есть
    (no-op fallback). Пустая строка возвращается без изменений."""
    if not value:
        return value
    f = _get_fernet()
    if f is None:
        return value
    token = f.encrypt(value.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt_secret(value: Optional[str]) -> str:
    """Расшифровать. Если префикса нет — считаем legacy plain-значением и
    возвращаем как есть. Если расшифровать не удалось (битый токен,
    secret_key сменился) — лог-варнинг и возврат пустой строки, чтобы не
    падать прод."""
    if not value:
        return ""
    if not value.startswith(ENC_PREFIX):
        # legacy plain
        return value
    f = _get_fernet()
    if f is None:
        log.error(
            "crypto: значение зашифровано (enc: префикс), но secret_key "
            "не настроен. Не могу расшифровать."
        )
        return ""
    try:
        return f.decrypt(value[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.error(
            "crypto: InvalidToken при расшифровке — возможно, secret_key "
            "сменился. Перевыпусти PlanFact-ключ через /settings."
        )
        return ""


def make_link_token(payload: str) -> Optional[str]:
    """Короткоживущий подписанный токен (Fernet, со встроенным timestamp).

    Используется для переноса идентичности pnl-юзера через внешний OAuth-раунд
    привязки Dodo IS: cookie `pnl_session` (SameSite=Lax) не доживает до возврата
    на `/auth/link` после редиректа через auth.dodois.io, поэтому identity несём
    в `return_to`. Если secret_key не настроен → None (вызов откатится на cookie).
    """
    f = _get_fernet()
    if f is None:
        return None
    return f.encrypt(payload.encode("utf-8")).decode("ascii")


def read_link_token(token: str, max_age_sec: int = 600) -> Optional[str]:
    """Проверить link-token и вернуть payload, либо None (битый/протухший/нет ключа)."""
    if not token:
        return None
    f = _get_fernet()
    if f is None:
        return None
    try:
        return f.decrypt(token.encode("ascii"), ttl=max_age_sec).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
