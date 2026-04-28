"""Audit log helper.

log_audit(session, action, ...) добавляет запись в pnl_service.audit_log в
текущей транзакции. Не делает commit — пусть вызывающий контролирует.

Чувствительные значения (пароли, full PF API key, full Dodo IS access_token)
НИКОГДА не пишем в details. Если нужно отметить «что-то поменяли» — кладём
boolean-флаги, имена полей или маски.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog


# Канонические action-коды. Хранятся как enum-like константы — чтобы grep'ом
# в коде сразу было видно, какие события мы пишем, а UI мог их группировать.
ACTION_LOGIN_SUCCESS       = "login_success"
ACTION_LOGIN_FAILED        = "login_failed"
ACTION_LOGIN_RATE_LIMITED  = "login_rate_limited"
ACTION_LOGOUT              = "logout"
ACTION_PASSWORD_CHANGED    = "password_changed"
ACTION_INTEGRATIONS_UPDATED = "integrations_updated"
ACTION_SESSION_REVOKED     = "session_revoked"
ACTION_ADMIN_USER_CREATED  = "admin_user_created"
ACTION_ADMIN_USER_DELETED  = "admin_user_deleted"
ACTION_ADMIN_USER_UPDATED  = "admin_user_updated"
ACTION_ADMIN_PASSWORD_RESET = "admin_password_reset"


def _ip_ua(request: Optional[Request]) -> tuple[Optional[str], Optional[str]]:
    if request is None:
        return None, None
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


async def log_audit(
    session: AsyncSession,
    action: str,
    *,
    user_id: Optional[int] = None,
    request: Optional[Request] = None,
    details: Optional[dict] = None,
) -> None:
    """Добавить запись audit_log. Без commit — вызывающий коммитит транзакцию."""
    ip, ua = _ip_ua(request)
    entry = AuditLog(
        user_id=user_id,
        action=action,
        details=details if details else None,
        ip=ip,
        user_agent=ua,
    )
    session.add(entry)
    await session.flush()
