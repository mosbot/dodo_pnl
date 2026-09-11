#!/usr/bin/env python3
"""Собирает docs/dodois-api-facts.md из docs/dodois-openapi/*.yaml.

Ничего не выводит и не додумывает: в выходном файле только то, что дословно
записано в официальных спеках Dodo Brands. Проза живёт в dodois-api.md,
факты — здесь; при расхождении правы спеки или живой вызов.

Запуск: python3 _scripts/gen_dodois_facts.py
"""
from __future__ import annotations

import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPECS = ROOT / "docs" / "dodois-openapi"
OUT = ROOT / "docs" / "dodois-api-facts.md"

# dodo-is-api.yaml — предыдущая выгрузка того же API, v2 её замещает.
SKIP = {"dodo-is-api.yaml"}

METHODS = ("get", "post", "put", "patch", "delete")


def load(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def servers_of(node: dict) -> list[tuple[str, str]]:
    return [(s.get("url", ""), s.get("description", "")) for s in node.get("servers", [])]


def scopes_of(op: dict, spec: dict) -> list[str]:
    out: list[str] = []
    for entry in op.get("security", spec.get("security", [])) or []:
        for names in entry.values():
            out.extend(names or [])
    return sorted(set(out))


def limits_of(op: dict, spec: dict) -> list[str]:
    """Числовые границы, записанные в спеке: maxLength/maximum/enum у query."""
    out = []
    for p in op.get("parameters", []) or []:
        if "$ref" in p:
            ref = p["$ref"].rsplit("/", 1)[-1]
            p = (spec.get("components", {}).get("parameters", {}) or {}).get(ref, {})
        if p.get("in") != "query":
            continue
        sch = p.get("schema", {}) or {}
        bits = []
        for key, label in (("maximum", "max"), ("minimum", "min"),
                           ("maxLength", "maxLen"), ("maxItems", "maxItems")):
            if key in sch:
                bits.append(f"{label}={sch[key]}")
        if "enum" in sch:
            bits.append("enum=" + "|".join(str(v) for v in sch["enum"]))
        if "default" in sch:
            bits.append(f"default={sch['default']}")
        if bits:
            out.append(f"`{p.get('name')}` {', '.join(bits)}")
    return out


def country_in_path(urls: list[str]) -> str:
    """Есть ли в базовом URL сегмент страны и/или бренда."""
    has_country = any(re.search(r"/(dodopizza|drinkit)/[a-z]{2}(/|$)", u) for u in urls)
    has_brand = any(re.search(r"/(dodopizza|drinkit)(/|$)", u) for u in urls)
    if has_country:
        return "страна И бренд"
    if has_brand:
        return "только бренд"
    return "нет"


def main() -> int:
    files = sorted(p for p in SPECS.glob("*.yaml") if p.name not in SKIP)
    if not files:
        print(f"нет спек в {SPECS}", file=sys.stderr)
        return 1

    lines: list[str] = []
    w = lines.append

    w("# Dodo IS API — факты из официальных спек")
    w("")
    w(f"Сгенерировано `_scripts/gen_dodois_facts.py` из `docs/dodois-openapi/*.yaml`")
    w("(OpenAPI от Dodo Brands). **Руками не править — перегенерировать.**")
    w("")
    w("Здесь только то, что дословно записано в спеках. Ничего не выведено и не")
    w("дополнено по памяти. Если утверждение в любой нашей прозе расходится с этим")
    w("файлом — правы спеки или живой вызов, не проза.")
    w("")
    w("Чего в спеках НЕТ и что отсюда узнать нельзя: какие поля реально приходят")
    w("сверх схемы, какие скоупы ручка потребует на самом деле (в спеке заявлен")
    w("минимум — см. инцидент со `shared` в `dodois-api.md`), и работает ли ручка")
    w("в конкретной стране.")
    w("")

    # --- страны -----------------------------------------------------------
    v2 = SPECS / "dodo-is-api-v2.yaml"
    if v2.exists():
        w("## 1. Страны и хосты")
        w("")
        w("Полный список серверов из `dodo-is-api-v2.yaml` — единственное место,")
        w("где Dodo перечисляет страны поимённо. `.io` и `.com` — разные кластеры;")
        w("хост определяется страной, а не нашим удобством.")
        w("")
        by_host: dict[str, list[str]] = {}
        for url, desc in servers_of(load(v2)):
            m = re.match(r"https://(api\.dodois\.(?:io|com))/(\w+)/([a-z]{2})$", url)
            if not m:
                continue
            host, brand, cc = m.groups()
            name = desc.split("—")[0].strip()
            by_host.setdefault(f"{host} · {brand}", []).append(f"{cc} ({name})")
        w("| Хост и бренд | Страны |")
        w("|---|---|")
        for key in sorted(by_host):
            w(f"| `{key}` | {', '.join(sorted(by_host[key]))} |")
        w("")
        io_cc = sorted({c.split(" ")[0] for k, v in by_host.items()
                        if "dodois.io" in k and "dodopizza" in k for c in v})
        w(f"Кластер `api.dodois.io` для Dodo Pizza — ровно {len(io_cc)} стран: "
          f"{', '.join(io_cc)}. Все остальные — на `api.dodois.com`.")
        w("")
        dp = sorted({c.split(" ")[0] for k, v in by_host.items()
                     if "dodopizza" in k for c in v})
        w(f"Полный набор стран Dodo Pizza ({len(dp)}) — любая наша таблица стран "
          "(numeric→alpha-2, валюта, часовой пояс) должна покрывать его целиком, "
          "иначе непокрытая страна молча схлопнется в дефолт:")
        w("")
        w("```")
        w(" ".join(dp))
        w("```")
        w("")

    # --- базовые URL ------------------------------------------------------
    w("## 2. Базовый URL по разделам")
    w("")
    w("Ключевая ловушка: сегмент страны есть НЕ во всех разделах. Код, который")
    w("клеит `/{business}/{country}` ко всему подряд, ломается ровно здесь.")
    w("")
    w("| Файл спеки | API | Базовые URL | Страна в пути |")
    w("|---|---|---|---|")
    for f in files:
        spec = load(f)
        urls = [u for u, _ in servers_of(spec)]
        shown = sorted({re.sub(r"/(?:dodopizza|drinkit)/[a-z]{2}$", "/{brand}/{country}", u)
                        for u in urls})
        if len(shown) > 3:
            shown = shown[:3] + [f"… ещё {len(shown) - 3}"]
        w(f"| `{f.name}` | {spec.get('info', {}).get('title', '')} | "
          f"{'<br>'.join('`' + s + '`' for s in shown)} | {country_in_path(urls)} |")
    w("")

    # --- эндпоинты --------------------------------------------------------
    w("## 3. Эндпоинты")
    w("")
    total = 0
    for f in files:
        spec = load(f)
        w(f"### {spec.get('info', {}).get('title', f.name)} (`{f.name}`)")
        w("")
        for path, item in (spec.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            # у отдельных путей свои серверы (ratings-api так делает)
            own = servers_of(item)
            if own:
                w(f"*Свои серверы:* {', '.join('`' + u + '`' for u, _ in own)}")
            for method in METHODS:
                op = item.get(method)
                if not isinstance(op, dict):
                    continue
                total += 1
                summary = op.get("summary", "").strip()
                w(f"- **`{method.upper()} {path}`** — {summary}")
                sc = scopes_of(op, spec)
                if sc:
                    w(f"  - scopes: {', '.join('`' + s + '`' for s in sc)}")
                lim = limits_of(op, spec)
                if lim:
                    w(f"  - границы из спеки: {'; '.join(lim)}")
        w("")

    w(f"Всего операций в спеках: {total}.")
    w("")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{OUT} — {len(lines)} строк, {total} операций из {len(files)} спек")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
