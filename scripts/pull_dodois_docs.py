#!/usr/bin/env python3
"""Pull Dodo IS API definitions from docs.dodois.io (Stoplight) and dump
a self-contained markdown reference into outputs/pnl-service/docs/.

No auth required — Stoplight serves the project nodes publicly via the
project_id `cHJqOjExMTA4MQ` (base64 of `prj:111081`).
"""
from __future__ import annotations

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROJECT = "cHJqOjExMTA4MQ"  # docs.dodois.io main API
BASE = f"https://stoplight.io/api/v1/projects/{PROJECT}"
TOC_URL = f"{BASE}/table-of-contents?branch=main"

# Все интересные нам ручки (id -> человекочитаемый title)
ENDPOINTS = {
    # Доставка
    "6149483e4dfb1": "Доставка → Статистика",
    "f3c261f246fc0": "Доставка → Сертификаты за опоздание",
    "14c586221ab77": "Доставка → Заказы курьеров",
    "7897c9e041974": "Доставка → Сектора доставки",
    "3e817cbe2a17a": "Доставка → Стоп-продажи по секторам",
    "dc441a810d936": "Доставка → Эффективность (in_development)",
    # Производство
    "c48a37d12f9e9": "Производство → Время выдачи заказа",
    "d7fe8cbb592b4": "Производство → Метрики с трекинга (Сводные)",
    "e82c12e60120b": "Производство → Статистика выдачи заказов",
    "0693bf4a07b8e": "Производство → Производительность",
    "6bcaeb26e9f28": "Производство → Стоп-продажи по каналам продаж",
    "846af18915ab3": "Производство → Стоп-продажи по ингредиентам",
    "f90f05153cfac": "Производство → Стоп-продажи по продуктам",
    "bf2d9afb49743": "Производство → Нагрузка на заведение по заказам",
    "56b11e6c01547": "Производство → Нагрузка на заведение по продуктам",
    # Команда
    "33e626f47ca51": "Команда → Список сотрудников",
    "16c2ad8c8d1eb": "Команда → Смены сотрудников (по пиццериям)",
    "9eeef5b727118": "Команда → Смены сотрудников (по идентификаторам)",
    "b7e33db9e95d9": "Команда → Курьеры на смене",
    "decb8a02719a1": "Команда → Расписания",
    "c482de2884d92": "Команда → Расписания: прогнозные метрики",
    "82dff94d4b268": "Команда → Должности сотрудников",
    "bbd33ff51ecba": "Команда → Вознаграждения (новое)",
    "c20d89d247492": "Команда → Премии",
    "889c94ce27740": "Команда → Количество открытых вакансий",
    "329e6574f6b44": "Команда → Открытые вакансии",
    "5cbec5e81c13a": "Команда → Информация о сотруднике",
    "726d2fd7e4b16": "Команда → Поиск сотрудников",
    # Учёт
    "5237b60b3775a": "Учёт → Продукты",
    "21e111d38ce83": "Учёт → Информация о продукте",
    "50d94568862f9": "Учёт → Сырьё",
    "559eaa3841528": "Учёт → Списанные продукты",
    "ef7cce54fafcc": "Учёт → Списанное сырьё",
    "17ff69b77061f": "Учёт → Забракованные продукты",
    "9829b528bdd4f": "Учёт → Питание персонала",
    "3d86ebf481dd7": "Учёт → Отмены заказов",
    "5cdae1cb7443f": "Учёт → Продажи",
    "cc2c7af8bbbaf": "Учёт → Расход сырья за период",
    "1e0a3c3ef2950": "Учёт → Перемещения сырья",
    "f343646aeca86": "Учёт → Приходы сырья",
    "bb6453ee128e5": "Учёт → Складские остатки",
    "2a18ded7d745e": "Учёт → Расход теста",
    # Финансы
    "42e6d9ec7923b": "Финансы → Дневные продажи по стране",
    "442ee262c9bab": "Финансы → Дневные продажи по заведениям",
    "774e372ed5435": "Финансы → Месячные продажи по стране",
    "7b9d046c52353": "Финансы → Месячные продажи по заведениям",
    "837c8c5582f92": "Финансы → Продажи по стране за период",
    "4e7fb7a30a284": "Финансы → Продажи по заведениям за период",
    # Заказы
    "ccadc272646ef": "Заказы → Статистика по новым клиентам",
    # Заведения
    "b73aa5a9c1052": "Заведения → Смены заведений",
    "88554db5564c2": "Заведения → Информация о заведениях",
    "f901f00320572": "Заведения → Информация о пиццериях/кофейнях",
    "1bd039683b54a": "Заведения → Информация о ПРЦ",
    "e403d490037aa": "Заведения → Производственные станции",
    "fdd52b85049d6": "Заведения → Цели на месяц (GET)",
    "030305f218aa4": "Заведения → Цели на месяц (PATCH)",
    # Оргструктура
    "064bf6a8adf46": "Оргструктура → Список юрлиц",
    "36cacbb8c35f3": "Оргструктура → Список типов юрлиц",
    "e1eb20a227177": "Оргструктура → Список населённых пунктов",
}


def fetch(node_id: str) -> dict:
    url = f"{BASE}/nodes/{node_id}?branch=main&deref=optimizedBundle"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
    j = json.loads(raw)
    return json.loads(j["data"])


def _resolve(schema: dict | None, bundled: dict, _seen: set | None = None) -> dict | None:
    """Inline $ref pointing to #/__bundled__/Name. Avoids infinite recursion."""
    if not isinstance(schema, dict):
        return schema
    seen = _seen or set()
    ref = schema.get("$ref")
    if ref and ref.startswith("#/__bundled__/"):
        name = ref.split("/", 2)[-1]
        if name in seen:
            return {"type": "ref", "description": f"recursive ref to {name}"}
        seen = seen | {name}
        target = bundled.get(name)
        if not target:
            return {"type": "ref", "description": f"unresolved {name}"}
        merged = dict(target)
        for k, v in schema.items():
            if k != "$ref" and v is not None:
                merged[k] = v
        return _resolve(merged, bundled, seen)
    return schema


def flat_props(schema: dict | None, bundled: dict, prefix: str = "",
               depth: int = 0, _seen: set | None = None) -> list[dict]:
    """Compress a JSON-schema (with $ref bundling) into a flat list."""
    if not schema or depth > 5:
        return []
    schema = _resolve(schema, bundled, _seen)
    if not schema:
        return []
    if schema.get("allOf"):
        # Merge first non-trivial allOf branch
        first = schema["allOf"][0]
        schema = _resolve(first, bundled, _seen) or {}
    s = schema
    out: list[dict] = []
    if s.get("type") == "object" and s.get("properties"):
        for k, v in s["properties"].items():
            path = f"{prefix}.{k}" if prefix else k
            vr = _resolve(v, bundled, _seen) or {}
            t = vr.get("type") or ("ref" if vr.get("allOf") or vr.get("oneOf") else "?")
            desc = " ".join((vr.get("description") or v.get("description") or "").split())[:160]
            out.append({"p": path, "t": t, "d": desc})
            if vr.get("type") == "object":
                out.extend(flat_props(vr, bundled, path, depth + 1, _seen))
            if vr.get("type") == "array" and vr.get("items"):
                out.extend(flat_props(vr["items"], bundled, path + "[]", depth + 1, _seen))
    if s.get("type") == "array" and s.get("items"):
        out.extend(flat_props(s["items"], bundled, prefix + "[]", depth + 1, _seen))
    return out


def render(node_id: str, title: str, op: dict) -> str:
    lines: list[str] = []
    lines.append(f"### {title}")
    lines.append(f"**`{(op.get('method') or '').upper()} {op.get('path') or ''}`**")
    desc = " ".join((op.get("description") or "").split())
    if desc:
        lines.append(f"> {desc[:400]}")
    qs = op.get("request", {}).get("query") or []
    if qs:
        lines.append("")
        lines.append("Query:")
        for q in qs:
            t = (q.get("schema") or {}).get("type", "?")
            req = ", required" if q.get("required") else ""
            d = " ".join((q.get("description") or "").split())[:160]
            lines.append(f"- `{q['name']}` ({t}{req}) — {d}")
    body = op.get("request", {}).get("body")
    if body:
        mts = ", ".join(c.get("mediaType", "") for c in (body.get("contents") or []))
        lines.append("")
        lines.append(f"Body: {mts}")
    resp200 = None
    for r in op.get("responses") or []:
        if str(r.get("code")) == "200":
            resp200 = r
            break
    if not resp200 and op.get("responses"):
        resp200 = op["responses"][0]
    if resp200:
        c = (resp200.get("contents") or [{}])[0]
        sch = c.get("schema")
        bundled = op.get("__bundled__") or {}
        fields = flat_props(sch, bundled)
        if fields:
            lines.append("")
            lines.append(f"Response ({c.get('mediaType') or '?'}):")
            for f in fields[:80]:
                d = f" — {f['d']}" if f["d"] else ""
                lines.append(f"- `{f['p']}`: {f['t']}{d}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    results: dict[str, dict] = {}

    def task(nid: str) -> tuple[str, dict | None, str | None]:
        try:
            return nid, fetch(nid), None
        except Exception as e:  # noqa: BLE001
            return nid, None, str(e)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for nid, op, err in pool.map(task, ENDPOINTS.keys()):
            if err:
                print(f"ERR {nid}: {err}", file=sys.stderr)
                continue
            results[nid] = op

    sections: dict[str, list[str]] = {}
    for nid, title in ENDPOINTS.items():
        if nid not in results:
            continue
        head = title.split(" → ", 1)[0]
        sections.setdefault(head, []).append(render(nid, title, results[nid]))

    # Auto-generated tail: everything below the AUTOGEN_MARKER is rewritten.
    AUTOGEN_MARKER = "# Полный референс (auto-generated)"
    body_lines: list[str] = [AUTOGEN_MARKER, ""]
    body_lines.append(
        "Ниже — все разобранные ручки: метод, путь, query, поля ответа. "
        "Сгенерировано `_scripts/pull_dodois_docs.py` (Stoplight nodes API)."
    )
    body_lines.append("")
    for head in [
        "Доставка", "Производство", "Команда", "Учёт", "Финансы",
        "Заказы", "Заведения", "Оргструктура",
    ]:
        if head not in sections:
            continue
        body_lines.append(f"## {head}")
        body_lines.append("")
        body_lines.extend(sections[head])
    body = "\n".join(body_lines)

    import os
    # Скрипт лежит в pnl-service/scripts/, артефакт — в pnl-service/docs/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    target = os.path.join(repo_root, "docs", "dodois-api.md")
    os.makedirs(os.path.dirname(target), exist_ok=True)

    DEFAULT_HEAD = (
        "# Dodo IS API — справочник эндпоинтов\n\n"
        "Источник: `docs.dodois.io`, project `cHJqOjExMTA4MQ` (Stoplight). "
        "Базовые URL по странам — `https://api.dodois.io/dodopizza/<country>` "
        "(RU: `/dodopizza/ru`, BY: `/dodopizza/by`, прочие на `api.dodois.com`).\n\n"
        "Авторизация: OAuth Bearer (`Authorization: Bearer <access_token>`). "
        "Список юнитов пользователя — `GET https://api.dodois.io/auth/roles/units`.\n\n"
        "Общие ограничения query-параметров (повторяются почти везде):\n"
        "- `units` — до 30 UUID через запятую без пробелов;\n"
        "- `from`/`to` — ISO 8601, обычно округление до часа, диапазон ≤ 31 день;\n"
        "- пагинация (где есть) — через `skip`+`take` или `nextPageToken`.\n\n---\n\n"
    )

    head = DEFAULT_HEAD
    if os.path.exists(target):
        with open(target, "r", encoding="utf-8") as f:
            existing = f.read()
        idx = existing.find(AUTOGEN_MARKER)
        if idx >= 0:
            head = existing[:idx]

    with open(target, "w", encoding="utf-8") as f:
        f.write(head + body + "\n")
    print(f"wrote {target}: {sum(1 for _ in results)} endpoints "
          f"(preserved {len(head)} chars of manual head)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
