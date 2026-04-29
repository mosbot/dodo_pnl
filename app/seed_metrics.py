"""Авто-сидинг pnl_metrics для существующих PlanFact-ключей.

Задача: после миграции 0007 в pnl_metrics пусто. Чтобы юзеру не пришлось
руками настраивать формулы, для каждого ключа с непустым шаблоном
генерим базовый набор формул на основе уже проставленных pnl_code'ов.

Семантика `[N]` в формулах: значение узла включает все потомки (rollup).
Поэтому для `UC = …` берём **верхний** узел с pnl_code='UC' (root sub-tree),
а не сумму всех листьев. Например: UC=[6]/[1] вместо UC=([7]+[8]+…)/[1].
Если в шаблоне у одного pnl_code есть несколько НЕ-вложенных корней —
суммируем их. Внутренних узлов с тем же кодом не учитываем (они и так
включены в rollup родителя).

Конкретно:
- REVENUE — корень с pnl_code='REVENUE' (минимальная глубина)
- Delivery revenue — узел с pnl_code='REVENUE' где в path_lc встречается
  «доставк» (для знаменателя DC)
- UC / LC / DC / RENT / MARKETING / FRANCHISE / MGMT / OTHER_OPEX — корни
  суб-деревьев с этим кодом, формула = (rollup-сумма) / [REVENUE].
  Для DC знаменатель = [delivery_revenue].
- TC = UC + LC + DC по тем же корням, делённое на REVENUE
- EBITDA, NET_PROFIT — is_calc узлы, формула = `[line_no]`, format=rub
- EBITDA_PCT, NET_PROFIT_PCT — процент от выручки

Если для какого-то кода в шаблоне нет узлов — пропускаем.

Запуск:
    python -m app.seed_metrics              # все ключи
    python -m app.seed_metrics --key 1      # конкретный
    python -m app.seed_metrics --dry-run    # показать SQL, не записать
    python -m app.seed_metrics --reseed     # перезаписать существующие

Идемпотентность: по умолчанию не трогает уже существующие записи в
pnl_metrics — только добавляет недостающие. С --reseed — DELETE+INSERT.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Optional

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.models import PlanfactKey
from .db import get_session_factory
from .models import PnLMetric, PnLTemplateNode


log = logging.getLogger("seed_metrics")


# (code, label, формат, is_target). Порядок = sort_order.
_METRIC_SPEC = [
    # (code, label, format, is_target, min_visibility_level)
    # Уровни: 0=видят все, 10=управляющий+, 30=территориальный+, 60=директор+, 100=партнёр.
    ("REVENUE", "Выручка", "rub", False, 0),
    ("UC", "Себестоимость продукции (UC)", "pct", True, 10),
    ("LC", "Оплата труда (LC)", "pct", True, 10),
    ("DC", "Расходы на доставку (DC)", "pct", True, 10),
    ("TC", "Total Cost (UC+LC+DC)", "pct", True, 10),
    ("RENT", "Помещения и аренда", "pct", True, 30),
    ("MARKETING", "Маркетинг", "pct", True, 30),
    ("FRANCHISE", "Расходы на франшизу", "pct", False, 30),
    ("OTHER_OPEX", "Прочие операционные расходы", "pct", False, 30),
    ("MGMT", "Административный персонал", "pct", False, 60),
    ("EBITDA", "EBITDA", "rub", False, 60),
    ("NET_PROFIT", "Чистая прибыль", "rub", False, 100),
]
# EBITDA_PCT / NET_PROFIT_PCT не нужны — pct_of_revenue для них уже считается
# автоматически в _apply_metric_formulas (для format=rub возвращаем pct=value/revenue).
# Если хочется отдельной строкой — заведите вручную через UI «Метрики».


def _is_delivery_revenue(node: dict) -> bool:
    """Узел выручки канала «Доставка» — нужен как знаменатель для DC."""
    if node["pnl_code"] != "REVENUE":
        return False
    path_lc = (node.get("path_lc") or "").lower()
    return "доставк" in path_lc and "выручка" in path_lc


def _top_level_in_group(group_nodes: list[dict]) -> list[dict]:
    """Из набора узлов одной группы pnl_code оставить только «верхние» —
    те, у кого нет предка с тем же кодом в группе. Семантика [N] = rollup,
    поэтому потомки уже учтены в родителе.

    Иерархия определяется через path_lc: узел A потомок B, если path_lc(B)
    + " / " является префиксом path_lc(A).
    """
    paths = sorted(((n.get("path_lc") or "") for n in group_nodes), key=len)
    top_paths: list[str] = []
    for p in paths:
        if any(p.startswith(t + " / ") for t in top_paths):
            continue  # есть предок в группе — пропустить
        top_paths.append(p)
    top_set = set(top_paths)
    return [n for n in group_nodes if (n.get("path_lc") or "") in top_set]


def _build_formula(line_nos: list[int], denominator_line: int, fmt: str) -> str:
    """Формула sum-of-lines / denominator или просто `[line]` для rub."""
    if not line_nos:
        raise ValueError("empty line_nos")
    if fmt == "rub" and len(line_nos) == 1:
        return f"[{line_nos[0]}]"
    if len(line_nos) == 1:
        numerator = f"[{line_nos[0]}]"
    else:
        numerator = "(" + " + ".join(f"[{n}]" for n in line_nos) + ")"
    if fmt == "rub":
        return numerator
    return f"{numerator} / [{denominator_line}]"


def _gather_metrics_for_template(
    nodes: list[dict],
) -> list[tuple[str, str, str, str, bool, int]]:
    """Сгенерить (code, label, formula, format, is_target, sort_order) по
    шаблону. nodes — список dict из БД (line_no, depth, title, pnl_code,
    is_calc, path_lc).
    """
    # Группируем по pnl_code — все узлы с этим кодом
    nodes_by_code: dict[str, list[dict]] = {}
    revenue_line: Optional[int] = None
    delivery_line: Optional[int] = None
    ebitda_line: Optional[int] = None
    net_profit_line: Optional[int] = None

    for n in nodes:
        code = n.get("pnl_code")
        title_lc = (n.get("title") or "").lower().strip()
        if code:
            nodes_by_code.setdefault(code, []).append(n)
        # REVENUE root — узел с pnl_code='REVENUE' минимальной глубины,
        # т.е. сам корень «Выручка».
        if code == "REVENUE":
            if revenue_line is None or n["depth"] < (
                next((m["depth"] for m in nodes if m["line_no"] == revenue_line), 99)
            ):
                revenue_line = n["line_no"]
            if _is_delivery_revenue(n) and delivery_line is None:
                delivery_line = n["line_no"]
        # is_calc узлы для EBITDA/Net profit — по title.
        if n.get("is_calc"):
            if "ebitda" in title_lc and ebitda_line is None and "рентабельн" not in title_lc:
                ebitda_line = n["line_no"]
            if "чистая прибыль" in title_lc and net_profit_line is None and "рентабельн" not in title_lc:
                net_profit_line = n["line_no"]

    # Для каждой группы pnl_code оставляем только «верхние» узлы
    # (rollup-семантика [N] делает учёт потомков автоматически).
    top_lines_by_code: dict[str, list[int]] = {}
    for code, group in nodes_by_code.items():
        tops = _top_level_in_group(group)
        top_lines_by_code[code] = sorted(t["line_no"] for t in tops)
    by_code = top_lines_by_code

    out: list[tuple[str, str, str, str, bool, int, int]] = []
    for sort_order, (code, label, fmt, is_target, min_level) in enumerate(
        _METRIC_SPEC, start=1
    ):
        formula: Optional[str] = None
        denom = revenue_line
        if code == "REVENUE":
            if revenue_line is not None:
                formula = f"[{revenue_line}]"
        elif code == "TC":
            # Объединяем top-узлы UC+LC+DC и снова отсекаем вложенные.
            # У PiX DC лежит внутри LC, и при rollup-семантике [LC] уже
            # включает DC — иначе получим double counting.
            ucs = nodes_by_code.get("UC", [])
            lcs = nodes_by_code.get("LC", [])
            dcs = nodes_by_code.get("DC", [])
            combined = ucs + lcs + dcs
            if combined and revenue_line is not None:
                tops = _top_level_in_group(combined)
                line_nos = sorted(t["line_no"] for t in tops)
                formula = _build_formula(line_nos, revenue_line, "pct")
        elif code == "EBITDA":
            if ebitda_line is not None:
                formula = f"[{ebitda_line}]"
        elif code == "NET_PROFIT":
            if net_profit_line is not None:
                formula = f"[{net_profit_line}]"
        else:
            line_nos = by_code.get(code, [])
            if line_nos and revenue_line is not None:
                if code == "DC" and delivery_line is not None:
                    denom = delivery_line
                formula = _build_formula(line_nos, denom, fmt)

        if formula is None:
            log.info(
                "skip metric %s: нет источников (узлов с pnl_code или нужного calc-узла)",
                code,
            )
            continue
        out.append((code, label, formula, fmt, is_target, sort_order, min_level))
    return out


async def _seed_one_key(
    session: AsyncSession,
    pf_key_id: int,
    pf_key_name: str,
    *,
    reseed: bool,
    dry_run: bool,
) -> None:
    # Все узлы шаблона по этому ключу
    rows = (
        await session.execute(
            select(PnLTemplateNode).where(
                PnLTemplateNode.planfact_key_id == pf_key_id
            ).order_by(PnLTemplateNode.line_no)
        )
    ).scalars().all()
    if not rows:
        log.info("[%s] шаблон пуст — пропуск", pf_key_name)
        return
    nodes = [
        {
            "line_no": n.line_no,
            "depth": n.depth,
            "title": n.title,
            "pnl_code": n.pnl_code,
            "is_calc": bool(n.is_calc),
            "path_lc": n.path_lc,
        }
        for n in rows
    ]

    metrics = _gather_metrics_for_template(nodes)
    log.info("[%s] сгенерил %d метрик", pf_key_name, len(metrics))
    for code, _, formula, *_rest in metrics:
        log.info("  %s = %s", code, formula)

    if dry_run:
        return

    if reseed:
        await session.execute(
            delete(PnLMetric).where(PnLMetric.planfact_key_id == pf_key_id)
        )
        await session.flush()

    # Не перезатираем существующие (если не reseed): пропускаем те code,
    # что уже есть.
    existing = set(
        (
            await session.execute(
                select(PnLMetric.code).where(
                    PnLMetric.planfact_key_id == pf_key_id
                )
            )
        ).scalars().all()
    )

    inserted = 0
    for code, label, formula, fmt, is_target, sort_order, min_level in metrics:
        if code in existing:
            continue
        session.add(
            PnLMetric(
                planfact_key_id=pf_key_id,
                code=code,
                label=label,
                formula=formula,
                is_target=is_target,
                format=fmt,
                sort_order=sort_order,
                min_visibility_level=min_level,
            )
        )
        inserted += 1
    await session.commit()
    log.info("[%s] inserted %d (existed %d)", pf_key_name, inserted, len(existing))


async def main(
    *, only_key: Optional[int], reseed: bool, dry_run: bool
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    Session = get_session_factory()
    async with Session() as session:
        if only_key is not None:
            keys = (
                await session.execute(
                    select(PlanfactKey).where(PlanfactKey.id == only_key)
                )
            ).scalars().all()
            if not keys:
                log.error("ключ id=%d не найден", only_key)
                return
        else:
            keys = (
                await session.execute(select(PlanfactKey).order_by(PlanfactKey.id))
            ).scalars().all()
        for k in keys:
            await _seed_one_key(
                session, k.id, k.name,
                reseed=reseed, dry_run=dry_run,
            )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--key", type=int, default=None,
                   help="planfact_key.id — обработать один ключ")
    p.add_argument("--reseed", action="store_true",
                   help="DELETE+INSERT (по умолчанию — только добавляем недостающие)")
    p.add_argument("--dry-run", action="store_true",
                   help="показать что будет вставлено, не писать в БД")
    args = p.parse_args()
    asyncio.run(main(
        only_key=args.key,
        reseed=args.reseed,
        dry_run=args.dry_run,
    ))
