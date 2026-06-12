"""Адаптер PlanFact v2 reports/opu → cached_aggregates для build_pnl.

Миграция S20 (docs/audits/v2-reports-migration-plan.md). Ключевая идея:
v2 используется ТОЛЬКО как источник сумм (project_id, category_id) → amount.
Вся классификация (шаблон, pnl_code, каналы выручки) остаётся нашей —
через тот же cat_index, что и в raw-пути. Поэтому результат обязан
бит-в-бит совпадать с build_pnl на сырых операциях (см. POC: net profit
сошёлся до копейки).

Структура ответа v2 (probe 2026-06-10, /tmp/v2-projects-2026-05.json):
  data.operationCategoryByProjects:
    incomeItems[] / outcomeItems[] / dividendsItems[]
      — дерево категорий через details[]; суммы НЕ дублируются только
        на ЛИСТЬЯХ (узлы без details; у родителей — агрегаты детей);
      лист: operationCategoryId, totalValues[]:
        {projectId, factIncomeValue, factOutcomeValue, dividendsValue}
        (положительные; null = нет движений).

Семантика знаков идентична raw-циклу build_pnl (pnl.py):
  - доходы положительные (возвраты PF неттит внутри категории);
  - расходы положительные;
  - дивиденды (Capital) положительные (выплата уменьшает прибыль).
"""
from __future__ import annotations

from collections import defaultdict

from .pnl import REVENUE_CHANNELS


class V2AdapterError(Exception):
    """Ответ v2 не разбирается / пуст — caller делает fallback на raw."""


def _node_values(it: dict, value_fields: tuple[str, ...]) -> dict[str, float]:
    """per-project суммы узла из totalValues (первое непустое поле из списка)."""
    out: dict[str, float] = {}
    for tv in it.get("totalValues") or []:
        pid = tv.get("projectId")
        if pid is None:
            continue
        # «Без проекта» (isUndistributed) — пропускаем, как и raw-путь
        # пропускает operationParts без project.
        proj = tv.get("project") or {}
        if proj.get("isUndistributed"):
            continue
        for f in value_fields:
            v = tv.get(f)
            if v is not None:
                out[str(pid)] = out.get(str(pid), 0.0) + float(v)
                break
    return out


def _walk_own(items: list | None, value_fields: tuple[str, ...], sink) -> dict[str, float]:
    """Обойти дерево; для КАЖДОГО узла записать в sink его СОБСТВЕННЫЙ вклад.

    В PF операции можно вешать не только на листовые категории, но и прямо
    на родительские — у родителя totalValues = Σ детей + собственные
    операции. Поэтому own = node_totals − Σ(direct children totals).
    (Валидация 2026-06-10: листовой обход терял такие суммы — например
    «Прочие доходы» PiX, Δ 625 760 ₽.)

    Возвращает per-project totals поддерева (для расчёта own родителем).
    """
    level_totals: dict[str, float] = {}
    for it in items or []:
        node_vals = _node_values(it, value_fields)
        child_vals = _walk_own(it.get("details"), value_fields, sink)
        cid = str(it.get("operationCategoryId") or "")
        if cid and cid != "0":
            for pid, v in node_vals.items():
                own = v - child_vals.get(pid, 0.0)
                if abs(own) > 0.005:  # отсечь float-пыль
                    sink(cid, pid, own)
        for pid, v in node_vals.items():
            level_totals[pid] = level_totals.get(pid, 0.0) + v
    return level_totals


def v2_to_aggregates(report_data: dict, cat_index: dict) -> dict:
    """v2 data-конверт + cat_index → payload формата cached_aggregates.

    Возвращает {"totals": {"pid|code": amt}, "cat_totals": {"pid|cid": amt},
    "revenue_by_channel": {pid: {ch: amt}}, "active_project_ids": [...]}.

    Бросает V2AdapterError если в ответе нет ни income, ни outcome items
    (клиент без шаблона ОПУ / неожиданный формат) — caller уходит на raw.
    """
    ocp = (report_data or {}).get("operationCategoryByProjects") or {}
    if not (ocp.get("incomeItems") or ocp.get("outcomeItems")):
        raise V2AdapterError(
            "v2 reports/opu: пустые incomeItems/outcomeItems "
            "(нет шаблона ОПУ у клиента или сменился формат ответа)"
        )

    cat_totals: dict[tuple[str, str], float] = defaultdict(float)

    def sink(cid: str, pid: str, amount: float) -> None:
        cat_totals[(pid, cid)] += amount

    _walk_own(ocp.get("incomeItems"), ("factIncomeValue",), sink)
    _walk_own(ocp.get("outcomeItems"), ("factOutcomeValue",), sink)
    _walk_own(
        ocp.get("dividendsItems"),
        ("dividendsValue", "factOutcomeValue"), sink,
    )

    # totals и revenue_by_channel выводим из cat_totals × cat_index —
    # ровно та же логика, что в основном цикле build_pnl.
    totals: dict[tuple[str, str], float] = defaultdict(float)
    revenue_by_channel: dict[str, dict[str, float]] = defaultdict(
        lambda: {ch: 0.0 for ch in REVENUE_CHANNELS}
    )
    active_project_ids: set[str] = set()

    for (pid, cid), amt in cat_totals.items():
        active_project_ids.add(pid)
        info = cat_index.get(cid)
        if info is None:
            continue
        # Балансовые (Assets/Liabilities) в v2-ОПУ не приходят by design;
        # Capital — только дивиденды (отдельный блок). Дополнительный
        # фильтр по op_type не нужен.
        code = info.get("pnl_code")
        if not code:
            continue  # unclassified — в cat_totals остаётся для template_lines
        totals[(pid, code)] += amt
        if code == "REVENUE":
            ch = info.get("revenue_channel") or "other"
            revenue_by_channel[pid][ch] += amt

    return {
        "totals": {f"{p}|{c}": v for (p, c), v in totals.items()},
        "cat_totals": {f"{p}|{c}": v for (p, c), v in cat_totals.items()},
        "revenue_by_channel": {
            pid: dict(ch) for pid, ch in revenue_by_channel.items()
        },
        "active_project_ids": sorted(active_project_ids),
    }
