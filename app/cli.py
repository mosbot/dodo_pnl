"""CLI для администрирования pnl-service.

Запуск:
    cd /home/claude/pnl-service
    .venv/bin/python -m app.cli <command> [args]

Доступные команды:
    create-user    создать пользователя (логин/пароль), опционально админа
    set-password   сменить пароль существующему юзеру
    set-admin      выдать/забрать админ-права
    set-dodois     привязать имя из public.dodois_credentials
    set-planfact   записать PlanFact API key
    list-users     показать всех пользователей
    delete-user    удалить пользователя (без подтверждения, осторожно)

Все аргументы передаются через флаги: --username=..., --password=...
Это нужно, чтобы пароль не попал в shell history (использовать
`read -s` если интерактивно).
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .auth.models import User
from .auth.passwords import verify_password
from .auth.users import (
    create_user, get_user_by_username, list_users,
    set_admin, set_role, update_integrations, update_password,
)
from .db import get_session_factory
from . import store


async def _with_session(fn):
    factory = get_session_factory()
    async with factory() as s:
        return await fn(s)


# ---------------- commands ----------------

async def cmd_create_user(args: argparse.Namespace) -> int:
    pwd = args.password or getpass.getpass("Пароль: ")
    if len(pwd) < 8:
        print("Пароль должен быть минимум 8 символов", file=sys.stderr)
        return 2

    async def _do(s):
        try:
            u = await create_user(
                s,
                username=args.username,
                password=pwd,
                display_name=args.display_name,
                role=args.role,
                dodois_credentials_name=args.dodois,
            )
            await s.commit()
            return u
        except IntegrityError as e:
            await s.rollback()
            print(f"Ошибка: {e.orig}", file=sys.stderr)
            return None

    u = await _with_session(_do)
    if u is None:
        return 1
    print(f"OK: создан пользователь id={u.id} username={u.username} role={u.role}")
    return 0


async def cmd_set_password(args: argparse.Namespace) -> int:
    pwd = args.password or getpass.getpass("Новый пароль: ")
    if len(pwd) < 8:
        print("Пароль должен быть минимум 8 символов", file=sys.stderr)
        return 2

    async def _do(s):
        u = await get_user_by_username(s, args.username)
        if not u:
            print(f"Пользователь {args.username!r} не найден", file=sys.stderr)
            return False
        await update_password(s, u.id, pwd)
        await s.commit()
        return True

    return 0 if await _with_session(_do) else 1


async def cmd_set_role(args: argparse.Namespace) -> int:
    async def _do(s):
        u = await get_user_by_username(s, args.username)
        if not u:
            print(f"Пользователь {args.username!r} не найден", file=sys.stderr)
            return False
        await set_role(s, u.id, args.role)
        await s.commit()
        print(f"OK: {u.username} role={args.role}")
        return True

    return 0 if await _with_session(_do) else 1


async def cmd_set_admin(args: argparse.Namespace) -> int:
    """Legacy: set-admin true/false → set_role super_admin/user."""
    async def _do(s):
        u = await get_user_by_username(s, args.username)
        if not u:
            print(f"Пользователь {args.username!r} не найден", file=sys.stderr)
            return False
        new_role = "super_admin" if args.value else "user"
        await set_role(s, u.id, new_role)
        await s.commit()
        print(f"OK: {u.username} role={new_role}")
        return True

    return 0 if await _with_session(_do) else 1


async def cmd_set_dodois(args: argparse.Namespace) -> int:
    async def _do(s):
        u = await get_user_by_username(s, args.username)
        if not u:
            print(f"Пользователь {args.username!r} не найден", file=sys.stderr)
            return False
        await update_integrations(s, u.id, dodois_credentials_name=args.name)
        await s.commit()
        print(f"OK: {u.username}.dodois_credentials_name = {args.name!r}")
        return True

    return 0 if await _with_session(_do) else 1


async def cmd_set_planfact(args: argparse.Namespace) -> int:
    key = args.key or getpass.getpass("PlanFact API key: ")

    async def _do(s):
        u = await get_user_by_username(s, args.username)
        if not u:
            print(f"Пользователь {args.username!r} не найден", file=sys.stderr)
            return False
        await update_integrations(s, u.id, planfact_api_key=key)
        await s.commit()
        masked = (key[:4] + "..." + key[-4:]) if len(key) > 12 else "***"
        print(f"OK: {u.username}.planfact_api_key = {masked}")
        return True

    return 0 if await _with_session(_do) else 1


async def cmd_list_users(args: argparse.Namespace) -> int:
    async def _do(s):
        users = await list_users(s)
        # Простая таблица в stdout
        print(f"{'ID':>4} {'USERNAME':<20} {'DISPLAY':<20} {'ROLE':<14} {'DODOIS':<24} {'PF':<6}")
        print("-" * 96)
        for u in users:
            pf_status = "set" if u.planfact_key_id else "—"
            dodois = u.dodois_credentials_name or "—"
            display = u.display_name or "—"
            print(
                f"{u.id:>4} {u.username:<20} {display:<20} "
                f"{u.role:<14} {dodois:<24} {pf_status:<6}"
            )
        print(f"\nВсего: {len(users)}")
        return True

    return 0 if await _with_session(_do) else 1


async def cmd_set_project_active(args: argparse.Namespace) -> int:
    """Архивировать или активировать проект для конкретного юзера.

    Используется админом, например, чтобы скрыть тестовые/архивные проекты
    PlanFact. Идемпотентно: если записи в projects_config нет — создаст её.
    """
    async def _do(s):
        u = await get_user_by_username(s, args.username)
        if not u:
            print(f"Пользователь {args.username!r} не найден", file=sys.stderr)
            return False
        if not u.planfact_key_id:
            print(f"У {u.username} не задан planfact_key — нечего настраивать",
                  file=sys.stderr)
            return False
        await store.upsert_project_config(
            s, u.planfact_key_id, args.project_id, is_active=args.is_active
        )
        await s.commit()
        state = "активен" if args.is_active else "архивирован"
        print(f"OK: проект {args.project_id} для ключа {u.planfact_key_id} → {state}")
        return True

    return 0 if await _with_session(_do) else 1


async def cmd_list_user_projects(args: argparse.Namespace) -> int:
    """Показать настройки projects_config (is_active, display_name и т.п.) для юзера."""
    async def _do(s):
        u = await get_user_by_username(s, args.username)
        if not u:
            print(f"Пользователь {args.username!r} не найден", file=sys.stderr)
            return False
        if not u.planfact_key_id:
            print(f"У {u.username} не задан planfact_key.")
            return True
        cfg = await store.list_projects_config(s, u.planfact_key_id)
        if not cfg:
            print(f"У {u.username} нет переопределений projects_config "
                  f"(все проекты PlanFact активны по умолчанию).")
            return True
        print(f"{'PROJECT_ID':<14} {'ACTIVE':<8} {'DISPLAY_NAME':<24} {'DODOIS_UUID':<40}")
        print("-" * 92)
        for pid, c in sorted(cfg.items()):
            active = "yes" if c["is_active"] else "no"
            name = c["display_name"] or "—"
            uuid = c["dodo_unit_uuid"] or "—"
            print(f"{pid:<14} {active:<8} {name:<24} {uuid:<40}")
        return True

    return 0 if await _with_session(_do) else 1


async def cmd_delete_user(args: argparse.Namespace) -> int:
    async def _do(s):
        u = await get_user_by_username(s, args.username)
        if not u:
            print(f"Пользователь {args.username!r} не найден", file=sys.stderr)
            return False
        await s.delete(u)
        await s.commit()
        print(f"OK: пользователь {args.username} удалён")
        return True

    return 0 if await _with_session(_do) else 1


# ---------------- main ----------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="app.cli", description="pnl-service admin CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("create-user", help="создать пользователя")
    s.add_argument("--username", required=True)
    s.add_argument("--password", help="(если не указан — спросит интерактивно)")
    s.add_argument("--display-name", help="отображаемое имя")
    s.add_argument("--role", default="user",
                   choices=["super_admin", "network_admin", "user"],
                   help="роль (default: user)")
    s.add_argument("--dodois", help="имя в public.dodois_credentials")
    s.set_defaults(fn=cmd_create_user)

    s = sub.add_parser("set-password", help="сменить пароль")
    s.add_argument("--username", required=True)
    s.add_argument("--password", help="(если не указан — спросит интерактивно)")
    s.set_defaults(fn=cmd_set_password)

    s = sub.add_parser("set-role", help="сменить роль (super_admin/network_admin/user)")
    s.add_argument("--username", required=True)
    s.add_argument("--role", required=True,
                   choices=["super_admin", "network_admin", "user"])
    s.set_defaults(fn=cmd_set_role)

    s = sub.add_parser("set-admin", help="(legacy) выдать/забрать админ-права (super_admin)")
    s.add_argument("--username", required=True)
    s.add_argument("--value", type=lambda x: x.lower() in {"true", "yes", "1", "on"},
                   default=True, help="true/false (по умолчанию true)")
    s.set_defaults(fn=cmd_set_admin)

    s = sub.add_parser("set-dodois", help="привязать dodois_credentials_name")
    s.add_argument("--username", required=True)
    s.add_argument("--name", required=True, help="значение public.dodois_credentials.name")
    s.set_defaults(fn=cmd_set_dodois)

    s = sub.add_parser("set-planfact", help="записать PlanFact API key")
    s.add_argument("--username", required=True)
    s.add_argument("--key", help="(если не указан — спросит интерактивно)")
    s.set_defaults(fn=cmd_set_planfact)

    s = sub.add_parser("list-users", help="показать всех пользователей")
    s.set_defaults(fn=cmd_list_users)

    s = sub.add_parser("set-project-active",
                       help="архивировать/активировать проект для юзера")
    s.add_argument("--username", required=True)
    s.add_argument("--project-id", required=True)
    s.add_argument(
        "--is-active",
        type=lambda x: x.lower() in {"true", "yes", "1", "on"},
        required=True,
        help="true (активен) / false (архивирован)",
    )
    s.set_defaults(fn=cmd_set_project_active)

    s = sub.add_parser("list-user-projects",
                       help="показать projects_config переопределения юзера")
    s.add_argument("--username", required=True)
    s.set_defaults(fn=cmd_list_user_projects)

    s = sub.add_parser("delete-user", help="удалить пользователя")
    s.add_argument("--username", required=True)
    s.set_defaults(fn=cmd_delete_user)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    rc = asyncio.run(args.fn(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
