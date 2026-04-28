"""Расчёт P&L из списка операций PlanFact.

Структура одной операции:
    {
      "operationId": int,
      "operationType": "Income" | "Outcome" | ...,
      "operationDate": "YYYY-MM-DD",
      "operationParts": [
        {
          "value": 700.0,
          "project": {"projectId": 584301, "title": "Кубинка-1"},
          "operationCategory": {"operationCategoryId": 4334694, "title": "..."}
        },
        ...
      ]
    }

Мы перебираем operationParts, берём knowing project + category, направление знака — из родительской operationType, и по classify_category() получаем P&L-код.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import store


PNL_CODES = {
    "REVENUE": "Выручка",
    "UC": "Себестоимость продукции (UC)",
    "LC": "Оплата труда (LC)",
    "DC": "Расходы на доставку (DC)",
    "TC": "Total Cost (TC = UC+LC+DC)",
    "RENT": "Помещения и аренда",
    "MARKETING": "Маркетинг",
    "FRANCHISE": "Расходы на франшизу",
    "OTHER_OPEX": "Прочие операционные расходы",
    "OTHER_INCOME": "Прочие доходы",
    "MGMT": "Административный персонал",
    "INTEREST": "Проценты по кредитам",
    "TAX": "Налог на прибыль",
    "DIVIDENDS": "Дивиденды",
}

# Какая строка P&L к какому знаменателю прицепляется при расчёте % от выручки.
# DC — это метрика канала доставки, поэтому знаменатель — выручка доставки,
# а не общая выручка. Остальные — от общей выручки.
DENOMINATOR = {
    "DC": "delivery",
}

# Таргетируемые метрики. TC (Total Cost = UC+LC+DC) — это computed метрика,
# считается в target_report отдельно.
TARGETABLE_METRICS = ["UC", "LC", "DC", "TC", "RENT", "MARKETING"]
COMPUTED_TARGETABLE_METRICS = {"TC"}

REVENUE_CHANNELS = ("delivery", "restaurant", "takeaway", "other")


def _detect_revenue_channel(path_str: str) -> str:
    """Определить канал выручки по пути категории."""
    if "доставк" in path_str:
        return "delivery"
    if "ресторан" in path_str:
        return "restaurant"
    if "самовывоз" in path_str:
        return "takeaway"
    return "other"


async def _build_category_index(
    session, owner_id: int, planfact_key_id: int | None,
    categories: list[dict],
) -> dict[str, dict]:
    """id категории → {id, title, parent_id, path, op_type, activity_type, outcome_class, pnl_code}."""
    by_id: dict[str, dict] = {}
    raw_by_id: dict[str, dict] = {}
    for c in categories:
        cid = str(c.get("operationCategoryId") or c.get("id") or "")
        if not cid:
            continue
        raw_by_id[cid] = c
        parent_id = c.get("parentOperationCategoryId") or c.get("parentId")
        by_id[cid] = {
            "id": cid,
            "title": c.get("title") or c.get("name") or "",
            "parent_id": str(parent_id) if parent_id else None,
            "op_type": c.get("operationCategoryType"),          # Income/Outcome/...
            "activity_type": c.get("activityType"),              # Operating/Finance/...
            "outcome_class": c.get("outcomeClassification"),     # DirectVariable/IndirectFixed/None
        }

    # путь корень → лист
    def path(cid: str) -> list[str]:
        names: list[str] = []
        cur = by_id.get(cid)
        while cur:
            names.append(cur["title"])
            cur = by_id.get(cur["parent_id"]) if cur["parent_id"] else None
        return list(reversed(names))

    for cid, info in by_id.items():
        info["path"] = path(cid)
        info["path_str"] = " / ".join(info["path"]).lower()

    # Источники классификации, в порядке приоритета:
    #   1) шаблон, импортированный из экспорта ПланФакт (match по path)
    #   2) шаблон по последнему сегменту пути (leaf title)
    #   3) classify_category() — эвристика на словах в path
    # Шаблон — per planfact_key. Если у юзера нет ключа — только эвристика.
    # Точечный override живёт через PATCH /api/template/{id} (поле pnl_code
    # узла шаблона), отдельной таблицы маппинга больше нет.
    if planfact_key_id is not None:
        template_by_path = await store.template_path_to_code(session, planfact_key_id)
        template_by_leaf = (
            await store.template_leaf_title_to_code(session, planfact_key_id)
            if template_by_path else {}
        )
    else:
        template_by_path = {}
        template_by_leaf = {}
    for cid, info in by_id.items():
        path_str_orig = " / ".join(info["path"])
        path_lc = path_str_orig.lower()
        leaf_lc = (info["path"] or [""])[-1].lower().strip()
        code = (
            template_by_path.get(path_lc)
            or (template_by_leaf.get(leaf_lc) if template_by_leaf else None)
            or classify_category(info)
        )
        info["pnl_code"] = code
        info["revenue_channel"] = (
            _detect_revenue_channel(info["path_str"])
            if info["pnl_code"] == "REVENUE" else None
        )
        # Метка «зарплата управляющего» — в ветке LC в пути есть «управляющ»
        # ИЛИ последний сегмент пути содержит «управляющ». Нужна, чтобы по
        # настройке include_manager_in_lc переносить такие суммы в MGMT.
        path_str = info.get("path_str", "")
        leaf = (info.get("path") or [""])[-1].lower()
        info["is_manager_pay"] = (
            info["pnl_code"] == "LC" and ("управляющ" in path_str or "управляющ" in leaf)
        )
    return by_id


def classify_category(info: dict) -> str | None:
    """Определить P&L-код по атрибутам категории (path + op_type + activity + classification).

    Правила: сначала отсекаем не-P&L (Assets/Liabilities/Capital); потом в зависимости
    от Income/Outcome применяем свои правила. Порядок проверок расходов важен —
    более специфичные разделы (франшиза, налоги) проверяются раньше общих.
    """
    path_str = info.get("path_str", "")
    op_type = info.get("op_type")
    activity = info.get("activity_type")

    # Активы / пассивы / капитал (за исключением дивидендов) — не строка P&L
    if op_type in ("Assets", "Liabilities"):
        return None
    if op_type == "Capital":
        if "дивиденд" in path_str:
            return "DIVIDENDS"
        return None
    if op_type in (None, "None"):
        return None

    # --- Доходы ---
    if op_type == "Income":
        # Корень "Выручка" или "Выручка / …" — всегда REVENUE,
        # даже если в названии листа есть слова доставка/курьер.
        if "выручк" in path_str and "нераспред" not in path_str:
            return "REVENUE"
        if ("прочие доходы" in path_str or "излишек" in path_str
                or "корректировка" in path_str or "инкассац" in path_str):
            return "OTHER_INCOME"
        if activity == "Operating":
            return "REVENUE"
        return "OTHER_INCOME"

    # --- Расходы ---
    if op_type != "Outcome":
        return None

    # Сначала — специфические разделы (имеют приоритет)
    if "налог на прибыль" in path_str or "налог на доход" in path_str:
        return "TAX"
    if "процент" in path_str and ("кредит" in path_str or "займ" in path_str or "заем" in path_str):
        return "INTEREST"
    if "дивиденд" in path_str:
        return "DIVIDENDS"
    # Расходы на франшизу — весь раздел целиком (включая маркетинговый фонд / колл-центр)
    if "расходы на франшизу" in path_str or "роялти" in path_str:
        return "FRANCHISE"
    # Явные маркеры из плана счетов Dodo
    if "(dc)" in path_str or "расходы на доставку" in path_str:
        return "DC"
    if "(uc)" in path_str or "себестоим" in path_str or "kitchen cost" in path_str:
        return "UC"
    if "(lc)" in path_str or "оплата труда" in path_str:
        # "Оплата труда (LC)" может содержать подраздел "Расходы на доставку (DC)" —
        # но он уже отловлен выше по (DC).
        return "LC"
    # Админ-персонал / управление — выше RENT, чтобы "Аренда офиса управления"
    # не перебивала. В путях «Административный персонал» — отдельная ветка.
    if "административн" in path_str:
        return "MGMT"
    if "аренд" in path_str or "помещени" in path_str or "коммунал" in path_str:
        return "RENT"
    if "маркетинг" in path_str or "реклам" in path_str:
        return "MARKETING"

    # Всё остальное операционное — прочие opex
    if activity == "Operating":
        return "OTHER_OPEX"
    # Inv/Fin — в операционный P&L не попадают
    return None


async def build_pnl(
    *,
    session,                       # AsyncSession (не аннотируем — circular import)
    owner_id: int,
    planfact_key_id: int | None,   # для чтения шаблона/маппинга (общие на ключ)
    categories: list[dict],
    operations: list[dict],
    projects: list[dict],
    project_filter: list[str] | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    method: str = "accrual",
    require_committed: bool = True,
    period_month: str | None = None,
) -> dict:
    """Собрать P&L из операций.

    method="accrual" (по умолчанию) — сумма относится к тому периоду,
        в котором была *начислена* (operationParts[].calculationDate).
        Части без calculationDate или вне диапазона [date_start, date_end]
        отбрасываются. Если require_committed=True — берём только
        operationParts[].isCalculationCommitted == True (факт, а не план).

    method="cash" — берём всё как есть (фильтр сервера был по
        operationDate), клиентской дофильтрации нет.
    """
    # --- проекты ---
    projects_by_id = {
        str(p.get("projectId") or p.get("id")): (p.get("title") or p.get("name") or "")
        for p in projects
    }
    if project_filter:
        proj_set = set(project_filter)
    else:
        proj_set = set(projects_by_id.keys())

    # --- категории ---
    cat_index = await _build_category_index(session, owner_id, planfact_key_id, categories)

    # --- настройки ---
    include_manager_in_lc = await store.get_bool_setting(
        session, owner_id, "include_manager_in_lc", True
    )

    # --- агрегируем суммы по (project_id, pnl_code) и по (project_id, category_id) ---
    totals: dict[tuple[str, str], float] = defaultdict(float)
    cat_totals: dict[tuple[str, str], float] = defaultdict(float)
    # Выручка по каналам: project_id → {"delivery": X, "restaurant": Y, ...}
    revenue_by_channel: dict[str, dict[str, float]] = defaultdict(
        lambda: {ch: 0.0 for ch in REVENUE_CHANNELS}
    )
    # для отчёта «какие категории не удалось классифицировать»
    unclassified: dict[str, float] = defaultdict(float)

    active_project_ids: set[str] = set()

    # счётчики для диагностики фильтрации по calculationDate
    stats = {
        "parts_seen": 0,
        "parts_skipped_no_calc_date": 0,
        "parts_skipped_out_of_period": 0,
        "parts_skipped_not_committed": 0,
        "parts_kept": 0,
    }

    for op in operations:
        op_type = op.get("operationType")  # Income | Outcome | ...
        if op_type not in ("Income", "Outcome"):
            continue
        parts = op.get("operationParts") or []
        for part in parts:
            stats["parts_seen"] += 1

            # --- метод начисления: отсечь до всего остального ---
            if method == "accrual":
                calc_date = (part.get("calculationDate") or "")[:10]
                if not calc_date:
                    stats["parts_skipped_no_calc_date"] += 1
                    continue
                if date_start and calc_date < date_start:
                    stats["parts_skipped_out_of_period"] += 1
                    continue
                if date_end and calc_date > date_end:
                    stats["parts_skipped_out_of_period"] += 1
                    continue
                if require_committed and not part.get("isCalculationCommitted"):
                    stats["parts_skipped_not_committed"] += 1
                    continue

            project = part.get("project") or {}
            pid = str(project.get("projectId") or "")
            if not pid or pid not in proj_set:
                continue
            category = part.get("operationCategory") or {}
            cid = str(category.get("operationCategoryId") or "")
            if not cid or cid == "0":
                # 0 = не указана — пропускаем
                continue
            info = cat_index.get(cid)
            value = float(part.get("value") or 0)
            if info is None:
                continue
            stats["parts_kept"] += 1

            # Не-P&L категории (Активы/Капитал/Обязательства) — пропускаем без шума.
            info_op_type = info.get("op_type")
            if info_op_type not in ("Income", "Outcome"):
                continue

            code = info.get("pnl_code")
            if code is None:
                # Это Income/Outcome, но классификатор не справился — покажем в unclassified.
                unclassified[cid] += value
                continue

            # Настройка «не включать управляющего в LC» — перекидываем такие
            # части в административный персонал.
            if not include_manager_in_lc and info.get("is_manager_pay"):
                code = "MGMT"

            # Для возвратов может быть operationType=Outcome с Income-категорией
            # (или наоборот) — тогда сумма идёт с минусом.
            sign = 1 if info_op_type == op_type else -1
            signed = sign * value

            totals[(pid, code)] += signed
            cat_totals[(pid, cid)] += signed
            active_project_ids.add(pid)

            # Разрез выручки по каналам
            if code == "REVENUE":
                ch = info.get("revenue_channel") or "other"
                revenue_by_channel[pid][ch] += signed

    # Убираем из выборки проекты без движений, если пользователь не указал явный фильтр
    if project_filter:
        shown_project_ids = [pid for pid in project_filter if pid in projects_by_id]
    else:
        shown_project_ids = sorted(active_project_ids, key=lambda p: projects_by_id.get(p, p))

    # --- расчёт строк ---
    revenues = {pid: totals.get((pid, "REVENUE"), 0.0) for pid in shown_project_ids}
    delivery_revenues = {
        pid: revenue_by_channel[pid].get("delivery", 0.0)
        for pid in shown_project_ids
    }

    def denom_for(code: str, pid: str) -> float:
        """Знаменатель для расчёта процента. DC → выручка доставки, остальное → вся выручка."""
        if DENOMINATOR.get(code) == "delivery":
            return delivery_revenues.get(pid, 0.0)
        return revenues.get(pid, 0.0)

    def total_denom_for(code: str) -> float:
        if DENOMINATOR.get(code) == "delivery":
            return sum(delivery_revenues.values())
        return sum(revenues.values())

    def row(code: str, label: str, level: int, kind: str = "detail") -> dict:
        per_project: dict[str, dict] = {}
        total_amt = 0.0
        for pid in shown_project_ids:
            amt = totals.get((pid, code), 0.0)
            d = denom_for(code, pid)
            per_project[pid] = {
                "amount": amt,
                "pct_of_revenue": (amt / d) if d else None,
            }
            total_amt += amt
        td = total_denom_for(code)
        return {
            "code": code, "label": label, "level": level, "kind": kind,
            "denominator": DENOMINATOR.get(code, "total"),
            "projects": per_project,
            "total": {
                "amount": total_amt,
                "pct_of_revenue": (total_amt / td) if td else None,
            },
        }

    def computed_row(code: str, label: str, fn, level: int = 1, kind: str = "summary") -> dict:
        per_project: dict[str, dict] = {}
        total_amt = 0.0
        for pid in shown_project_ids:
            amt = fn(pid)
            d = revenues.get(pid, 0.0)
            per_project[pid] = {
                "amount": amt,
                "pct_of_revenue": (amt / d) if d else None,
            }
            total_amt += amt
        total_rev = sum(revenues.values())
        return {
            "code": code, "label": label, "level": level, "kind": kind,
            "denominator": "total",
            "projects": per_project,
            "total": {
                "amount": total_amt,
                "pct_of_revenue": (total_amt / total_rev) if total_rev else None,
            },
        }

    variable_codes = ["UC", "LC", "DC", "RENT", "MARKETING", "FRANCHISE", "OTHER_OPEX"]
    tc_codes = ["UC", "LC", "DC"]

    def total_cost(pid: str) -> float:
        return sum(totals.get((pid, c), 0.0) for c in tc_codes)

    def variable_sum(pid: str) -> float:
        return sum(totals.get((pid, c), 0.0) for c in variable_codes)

    def margin(pid: str) -> float:
        return revenues[pid] - variable_sum(pid)

    def operating_profit(pid: str) -> float:
        # Прибыль от операционной деятельности: выручка минус все операционные
        # расходы (UC+LC+DC+RENT+MARKETING+FRANCHISE+OTHER_OPEX+MGMT). Прочие
        # доходы/финансовые/налоги — ниже строки.
        return margin(pid) - totals.get((pid, "MGMT"), 0.0)

    def ebitda(pid: str) -> float:
        # EBITDA = Operating Profit + Прочие доходы.
        return operating_profit(pid) + totals.get((pid, "OTHER_INCOME"), 0.0)

    def net_profit(pid: str) -> float:
        return ebitda(pid) - totals.get((pid, "INTEREST"), 0.0) - totals.get((pid, "TAX"), 0.0)

    lines = [
        row("REVENUE", "Выручка", 1, "header"),
        row("UC", "Себестоимость продукции (UC)", 2),
        row("LC", "Оплата труда (LC)", 2),
        row("DC", "Расходы на доставку (DC)", 2),
        computed_row("TC", "Total Cost (TC = UC+LC+DC)", total_cost, 1, "summary"),
        row("RENT", "Помещения и аренда", 2),
        row("MARKETING", "Маркетинг", 2),
        row("FRANCHISE", "Расходы на франшизу", 2),
        row("OTHER_OPEX", "Прочие операционные расходы", 2),
        computed_row("MARGIN", "Маржинальная прибыль", margin, 1, "summary"),
        row("MGMT", "Административный персонал", 2),
        computed_row("OPERATING_PROFIT", "Операционная прибыль", operating_profit, 1, "summary"),
        row("OTHER_INCOME", "Прочие доходы", 2),
        computed_row("EBITDA", "EBITDA", ebitda, 1, "summary"),
        row("INTEREST", "Проценты по кредитам", 2),
        row("TAX", "Налог на прибыль", 2),
        computed_row("NET_PROFIT", "Чистая прибыль", net_profit, 1, "final"),
        row("DIVIDENDS", "Дивиденды", 2),
    ]

    # --- цели ---
    raw_targets = await store.list_targets(session, owner_id)
    targets_index: dict[tuple[str, str], float] = {
        (t["project_id"], t["metric_code"]): t["target_pct"] for t in raw_targets
    }
    default_targets = await store.list_default_targets(session, owner_id)  # {metric: pct}

    def amount_for_metric(metric: str, pid: str) -> float:
        """Для TC — сумма UC+LC+DC; для прочих — totals по коду."""
        if metric == "TC":
            return total_cost(pid)
        return totals.get((pid, metric), 0.0)

    target_report = []
    for pid in shown_project_ids:
        for metric in TARGETABLE_METRICS:
            target = targets_index.get((pid, metric))
            source = "project"
            if target is None and metric in default_targets:
                target = default_targets[metric]
                source = "default"
            if target is None:
                continue
            amt = amount_for_metric(metric, pid)
            denom = denom_for(metric, pid)
            actual = (amt / denom) if denom else 0.0
            target_report.append({
                "project_id": pid,
                "project_name": projects_by_id.get(pid, pid),
                "metric": metric,
                "metric_label": PNL_CODES[metric],
                "denominator": DENOMINATOR.get(metric, "total"),
                "target_pct": target,
                "target_source": source,
                "actual_pct": actual,
                "delta_pct": actual - target,
                "status": "ok" if actual <= target else "over",
            })

    # --- детализация по категориям (для drill) ---
    category_breakdown: list[dict] = []
    for (pid, cid), amt in sorted(cat_totals.items(), key=lambda kv: -abs(kv[1])):
        info = cat_index.get(cid) or {}
        category_breakdown.append({
            "project_id": pid,
            "project_name": projects_by_id.get(pid, pid),
            "category_id": cid,
            "category_title": info.get("title", ""),
            "category_path": info.get("path", []),
            "pnl_code": info.get("pnl_code"),
            "amount": amt,
            "pct_of_revenue": (amt / revenues.get(pid, 0.0)) if revenues.get(pid) else None,
        })

    # --- Полная иерархия из шаблона ПланФакт ---
    # Для каждого узла шаблона считаем суммы по проектам, сворачиваем потомков в родителя.
    # Маппинг PlanFact-категория → template-узел: точное совпадение path_lc, иначе
    # самый глубокий префикс. Это позволяет показать пользовательскую иерархию,
    # а не наши 17 агрегатов.
    # computed_lines нужен, чтобы для is_calc-узлов («EBITDA», «Чистая прибыль» и т.п.)
    # отдать реально посчитанные значения, а не «—».
    template_lines = await build_template_breakdown(
        session=session,
        planfact_key_id=planfact_key_id,
        cat_index=cat_index,
        cat_totals=cat_totals,
        revenues=revenues,
        delivery_revenues=delivery_revenues,
        shown_project_ids=shown_project_ids,
        computed_lines=lines,
    )

    # --- projects_config (активность, отображаемое имя, сортировка) ---
    projects_cfg = await store.list_projects_config(session, owner_id)  # {pid: {is_active, display_name, sort_order}}

    def display_name_for(pid: str) -> str:
        cfg = projects_cfg.get(pid)
        if cfg and cfg.get("display_name"):
            return cfg["display_name"]
        return projects_by_id.get(pid, pid)

    # --- Ops-метрики за указанный месяц ---
    ops_data: dict[str, dict] = {}
    if period_month:
        ops_data = await store.list_ops_metrics(session, owner_id, period_month=period_month)
    # Включаем ops в список проектов и добавляем ops-статус в target_report.
    ops_targets = await store.list_ops_targets(session, owner_id)         # global defaults {code: value}
    ops_overrides = await store.ops_project_targets_map(session, owner_id)  # {pid: {code: value}}
    ops_target_report: list[dict] = []
    for pid in shown_project_ids:
        values = ops_data.get(pid) or {}
        per_project = ops_overrides.get(pid, {})
        for m in store.OPS_METRICS:
            code = m["code"]
            field = m["field"]
            direction = m["direction"]
            actual = values.get(field)
            # override > default
            if code in per_project:
                target = per_project[code]
                target_source = "project"
            else:
                target = ops_targets.get(code)
                target_source = "default"
            if target is None or actual is None:
                continue
            if direction == "higher":
                ok = actual >= target
                delta = actual - target
            else:
                ok = actual <= target
                delta = actual - target
            ops_target_report.append({
                "project_id": pid,
                "project_name": display_name_for(pid),
                "metric": code,
                "metric_label": m["label"],
                "metric_unit": m["unit"],
                "direction": direction,
                "target_value": target,
                "target_source": target_source,
                "actual_value": actual,
                "delta": delta,
                "status": "ok" if ok else "over",
            })

    # Объединяем P&L-таргеты и ops-таргеты в один отчёт (фронт разделит по direction).
    target_report_all = target_report + ops_target_report

    # Список проектов для ответа — с отображаемым именем и ops-значениями.
    projects_payload = []
    for pid in shown_project_ids:
        cfg = projects_cfg.get(pid, {})
        projects_payload.append({
            "id": pid,
            "name": display_name_for(pid),
            "planfact_name": projects_by_id.get(pid, pid),
            "is_active": bool(cfg.get("is_active", True)),
            "ops": ops_data.get(pid),
        })

    return {
        "projects": projects_payload,
        "lines": lines,
        "template_lines": template_lines,
        "targets": target_report_all,
        "category_breakdown": category_breakdown,
        "revenue_by_channel": {
            pid: dict(revenue_by_channel[pid]) for pid in shown_project_ids
        },
        "unclassified": [
            {
                "category_id": cid,
                "title": (cat_index.get(cid) or {}).get("title", ""),
                "amount": amt,
            }
            for cid, amt in sorted(unclassified.items(), key=lambda kv: -abs(kv[1]))[:20]
        ],
        "pnl_codes": PNL_CODES,
        "targetable_metrics": TARGETABLE_METRICS,
        "computed_targetable_metrics": sorted(COMPUTED_TARGETABLE_METRICS),
        "denominators": DENOMINATOR,
        "method": method,
        "period_month": period_month,
        "stats": stats,
        "settings": {
            "include_manager_in_lc": include_manager_in_lc,
        },
        "default_targets": default_targets,
        "ops_targets": ops_targets,
        "ops_project_targets": ops_overrides,     # {pid: {code: value}} — per-project override
        "ops_metrics_meta": store.OPS_METRICS,  # чтобы фронт знал labels/units/direction
    }


# Узлы шаблона, которые мы вообще не показываем на дашборде.
# По требованию: Дивиденды и Нераспределенная прибыль — не нужны в P&L-разрезе
# по проектам. Сравнение по точному заголовку (case-sensitive после strip).
HIDDEN_TEMPLATE_TITLES: set[str] = {
    "Дивиденды",
    "Нераспределенная прибыль",
}

# Маппинг calc-заголовка ПланФакт → код computed_row из build_pnl().
# Для этих строк отдаём реально посчитанные суммы и %.
CALC_TITLE_TO_LINE_CODE: dict[str, str] = {
    "Маржинальная прибыль": "MARGIN",
    "Операционная прибыль": "OPERATING_PROFIT",
    "EBITDA": "EBITDA",
    "Чистая прибыль (убыток)": "NET_PROFIT",
}

# Процентные calc-метрики: показываются как «X%» (без денежной суммы).
# Берём pct_of_revenue из соответствующей computed_row.
CALC_TITLE_TO_PCT_BASE: dict[str, str] = {
    "Маржинальность": "MARGIN",
    "Операционная рентабельность": "OPERATING_PROFIT",
    "Рентабельность по EBITDA": "EBITDA",
    "Рентабельность чистой прибыли": "NET_PROFIT",
}


async def build_template_breakdown(
    *,
    session,
    planfact_key_id: int | None,
    cat_index: dict[str, dict],
    cat_totals: dict[tuple[str, str], float],
    revenues: dict[str, float],
    delivery_revenues: dict[str, float],
    shown_project_ids: list[str],
    computed_lines: list[dict] | None = None,
) -> list[dict]:
    """Собрать P&L по иерархии шаблона ПланФакт.

    Маппинг PlanFact-категории на узел шаблона:
      1) точное совпадение нормализованного path
      2) самый глубокий узел, чей path является префиксом пути категории
         (для случаев, когда категория глубже шаблона)

    Дальше — постпорядковый rollup: amount узла = direct + сумма потомков.
    Возвращает плоский список в порядке sort_order; иерархия восстанавливается
    клиентом по parent_id/depth.
    """
    if planfact_key_id is None:
        return []
    nodes = await store.list_template_nodes(session, planfact_key_id)
    if not nodes:
        return []

    by_path_lc: dict[str, dict] = {}
    for n in nodes:
        # Если в шаблоне дублируются пути — оставляем самый глубокий.
        existing = by_path_lc.get(n["path_lc"])
        if existing is None or n["depth"] > existing["depth"]:
            by_path_lc[n["path_lc"]] = n

    # Сортируем узлы шаблона по убыванию глубины — для матчей сначала
    # смотрим самые глубокие совпадения.
    nodes_by_depth_desc = sorted(
        [n for n in nodes if n["path_lc"]],
        key=lambda n: -n["depth"]
    )

    # Только листовые узлы — для маппинга категорий имеет смысл цепляться к листам.
    leaf_nodes = [n for n in nodes_by_depth_desc if n["is_leaf"]]
    leaf_nodes_non_calc = [n for n in leaf_nodes if not n["is_calc"]]

    # Индекс «последний сегмент пути → список узлов». При нескольких совпадениях
    # по leaf-title мы предпочтём узел с тем же pnl_code, что у категории.
    leaves_by_leaf_title: dict[str, list[dict]] = defaultdict(list)
    for n in leaf_nodes_non_calc:
        leaf_title = n["path_lc"].rsplit(" / ", 1)[-1].strip()
        if leaf_title:
            leaves_by_leaf_title[leaf_title].append(n)

    children_of: dict[int | None, list[int]] = defaultdict(list)
    for n in nodes:
        children_of[n["parent_id"]].append(n["id"])

    cat_to_node: dict[str, int] = {}
    for cid, info in cat_index.items():
        path_lc = (info.get("path_str") or "").strip()
        if not path_lc:
            continue
        cat_leaf = path_lc.rsplit(" / ", 1)[-1].strip()
        cat_code = info.get("pnl_code")

        # 1) Точное совпадение пути.
        node = by_path_lc.get(path_lc)
        if node is not None:
            cat_to_node[cid] = node["id"]
            continue

        # 2) Шаблонный путь оканчивается на путь категории — у шаблона больше
        #    «обёртки» сверху (Доходы/Расходы/Переменные расходы/...), а у
        #    категории её нет. Берём самый глубокий такой узел.
        chosen = None
        suffix = " / " + path_lc
        for n in nodes_by_depth_desc:
            n_path = n["path_lc"]
            if n_path == path_lc or n_path.endswith(suffix):
                chosen = n
                break
        if chosen is not None:
            cat_to_node[cid] = chosen["id"]
            continue

        # 3) Путь категории глубже шаблона — категория «ниже» листа шаблона.
        #    Привязываем к этому листу-родителю.
        for n in nodes_by_depth_desc:
            n_path = n["path_lc"]
            if path_lc.startswith(n_path + " / "):
                chosen = n
                break
        if chosen is not None:
            cat_to_node[cid] = chosen["id"]
            continue

        # 4) Совпадение по последнему сегменту пути. При нескольких кандидатах
        #    предпочитаем лист с тем же pnl_code, что у категории.
        candidates = leaves_by_leaf_title.get(cat_leaf, [])
        if candidates:
            same_code = [n for n in candidates if cat_code and n["pnl_code"] == cat_code]
            chosen = (same_code[0] if same_code else candidates[0])
            cat_to_node[cid] = chosen["id"]
            continue

    # Прямые попадания: сумма категорий, привязанных непосредственно к узлу.
    direct: dict[tuple[str, int], float] = defaultdict(float)
    for (pid, cid), amt in cat_totals.items():
        nid = cat_to_node.get(cid)
        if nid is None:
            continue
        direct[(pid, nid)] += amt

    # Узел шаблона → набор PlanFact-category_id, которые на него мапятся напрямую.
    direct_cats_by_node: dict[int, set[str]] = defaultdict(set)
    for cid, nid in cat_to_node.items():
        direct_cats_by_node[nid].add(cid)

    # Rollup: считаем от листьев к корням (по убыванию depth).
    # Параллельно сворачиваем category_ids — для drill-down по ветке.
    rollup: dict[tuple[str, int], float] = defaultdict(float)
    cats_rollup: dict[int, set[str]] = defaultdict(set)
    for n in sorted(nodes, key=lambda x: -x["depth"]):
        nid = n["id"]
        for pid in shown_project_ids:
            v = direct.get((pid, nid), 0.0)
            for child_id in children_of.get(nid, []):
                v += rollup.get((pid, child_id), 0.0)
            if v != 0.0:
                rollup[(pid, nid)] = v
        # Сборка category_ids: свои + всех потомков (рекурсивно через rollup).
        cats_rollup[nid] |= direct_cats_by_node.get(nid, set())
        for child_id in children_of.get(nid, []):
            cats_rollup[nid] |= cats_rollup.get(child_id, set())

    # Индекс computed_lines по коду — для подстановки сумм/процентов в is_calc-узлы.
    line_by_code: dict[str, dict] = {}
    if computed_lines:
        line_by_code = {ln["code"]: ln for ln in computed_lines}

    out: list[dict] = []
    for n in nodes:
        title = (n.get("title") or "").strip()
        # 1) По требованию пользователя — не показывать в дашборде.
        if title in HIDDEN_TEMPLATE_TITLES:
            continue

        per_project: dict[str, dict] = {}
        is_delivery_metric = (n.get("pnl_code") == "DC")
        is_calc = bool(n.get("is_calc"))
        # Тип отображения: "money" (по умолчанию) или "pct" — для строк типа
        # «Рентабельность по EBITDA». Фронт понимает display_kind.
        display_kind = "money"
        total_block: dict

        if is_calc:
            # 2a) calc-строка с известной формулой → копируем из computed_row.
            line_code = CALC_TITLE_TO_LINE_CODE.get(title)
            line_block = line_by_code.get(line_code) if line_code else None
            # 2b) процентная calc-строка → берём pct из соответствующего line_code.
            pct_base_code = CALC_TITLE_TO_PCT_BASE.get(title)
            pct_base_block = line_by_code.get(pct_base_code) if pct_base_code else None

            if line_block is not None:
                for pid in shown_project_ids:
                    p = line_block["projects"].get(pid, {})
                    per_project[pid] = {
                        "amount": p.get("amount"),
                        "pct_of_revenue": p.get("pct_of_revenue"),
                    }
                total_block = {
                    "amount": line_block["total"]["amount"],
                    "pct_of_revenue": line_block["total"].get("pct_of_revenue"),
                }
            elif pct_base_block is not None:
                # Для процентных строк отдаём только pct (как amount=None),
                # но фронт благодаря display_kind="pct" нарисует pct в основном поле.
                display_kind = "pct"
                for pid in shown_project_ids:
                    p = pct_base_block["projects"].get(pid, {})
                    per_project[pid] = {
                        "amount": None,
                        "pct_of_revenue": p.get("pct_of_revenue"),
                    }
                total_block = {
                    "amount": None,
                    "pct_of_revenue": pct_base_block["total"].get("pct_of_revenue"),
                }
            else:
                # Неизвестная calc-строка — null, фронт покажет «—».
                for pid in shown_project_ids:
                    per_project[pid] = {"amount": None, "pct_of_revenue": None}
                total_block = {"amount": None, "pct_of_revenue": None}
        else:
            total_amt = 0.0
            for pid in shown_project_ids:
                amt = rollup.get((pid, n["id"]), 0.0)
                denom = (delivery_revenues.get(pid, 0.0) if is_delivery_metric
                         else revenues.get(pid, 0.0))
                per_project[pid] = {
                    "amount": amt,
                    "pct_of_revenue": (amt / denom) if denom else None,
                }
                total_amt += amt
            td = (sum(delivery_revenues.values()) if is_delivery_metric
                  else sum(revenues.values()))
            total_block = {
                "amount": total_amt,
                "pct_of_revenue": (total_amt / td) if td else None,
            }

        # category_ids — для drill-down: «покажи операции, относящиеся к этой
        # ветке шаблона». Для is_calc-узлов оставляем пустой список (drill отключен).
        node_cat_ids = [] if is_calc else sorted(cats_rollup.get(n["id"], set()))

        out.append({
            "id": n["id"],
            "parent_id": n["parent_id"],
            "depth": n["depth"],
            "title": n["title"],
            "path": n["path"],
            "is_leaf": n["is_leaf"],
            "is_calc": is_calc,
            "pnl_code": n["pnl_code"],
            "sort_order": n["sort_order"],
            "projects": per_project,
            "total": total_block,
            "display_kind": display_kind,
            "category_ids": node_cat_ids,
        })
    return out


def compare_pnl(current: dict, previous: dict, *, mode: str = "lfl") -> dict:
    """Добавить к current дельты относительно previous + положить compare-блок.

    compare-блок нужен фронту, чтобы отрисовать параллельные датасеты в графиках
    (выручка/прибыль/маржинальность/структура затрат) за прошлый период.
    mode — 'lfl' (тот же месяц прошлого года) или 'mom' (прошлый месяц).
    """
    prev_lines = {ln["code"]: ln for ln in previous["lines"]}
    for line in current["lines"]:
        prev = prev_lines.get(line["code"])
        if not prev:
            continue
        for pid, cur_proj in line["projects"].items():
            prev_proj = prev["projects"].get(pid)
            if not prev_proj:
                continue
            cur_proj["previous_amount"] = prev_proj["amount"]
            cur_proj["previous_pct_of_revenue"] = prev_proj.get("pct_of_revenue")
            if prev_proj["amount"]:
                cur_proj["delta_pct"] = (cur_proj["amount"] - prev_proj["amount"]) / abs(prev_proj["amount"])
            else:
                cur_proj["delta_pct"] = None
        line["total"]["previous_amount"] = prev["total"]["amount"]
        line["total"]["previous_pct_of_revenue"] = prev["total"].get("pct_of_revenue")
        if prev["total"]["amount"]:
            line["total"]["delta_pct"] = (
                (line["total"]["amount"] - prev["total"]["amount"])
                / abs(prev["total"]["amount"])
            )
    current["comparison"] = True
    current["compare"] = {
        "mode": mode,
        "lines": previous["lines"],
        "projects": previous.get("projects", []),
    }
    return current


def build_revenue_history(
    *,
    categories: list[dict],
    operations: list[dict],
    project_filter: list[str] | None,
    months: list[str],
    method: str = "accrual",
    require_committed: bool = False,
) -> dict:
    """Собрать выручку по месяцам.

    months — отсортированный список YYYY-MM, которые нужно заполнить
    (недостающие всё равно будут в ответе с amount=0).
    Возвращает:
        {
          "months": [YYYY-MM, ...],
          "totals": {month: amount},
          "projects": {pid: {month: amount}},
          "project_names": {pid: name},
        }

    Для accrual мы группируем по operationParts[].calculationDate (месяц),
    только REVENUE-категории.
    """
    cat_index = _build_category_index(categories)

    proj_set = set(project_filter) if project_filter else None
    projects_by_id: dict[str, str] = {}

    month_set = set(months)
    totals: dict[str, float] = {m: 0.0 for m in months}
    by_project: dict[str, dict[str, float]] = defaultdict(
        lambda: {m: 0.0 for m in months}
    )

    for op in operations:
        op_type = op.get("operationType")
        if op_type not in ("Income", "Outcome"):
            continue
        parts = op.get("operationParts") or []
        for part in parts:
            if method == "accrual":
                calc_date = (part.get("calculationDate") or "")[:10]
                if not calc_date:
                    continue
                if require_committed and not part.get("isCalculationCommitted"):
                    continue
                ym = calc_date[:7]
            else:
                ym = (op.get("operationDate") or "")[:7]
            if ym not in month_set:
                continue

            project = part.get("project") or {}
            pid = str(project.get("projectId") or "")
            if not pid:
                continue
            if proj_set is not None and pid not in proj_set:
                continue
            category = part.get("operationCategory") or {}
            cid = str(category.get("operationCategoryId") or "")
            if not cid or cid == "0":
                continue
            info = cat_index.get(cid)
            if info is None:
                continue
            info_op_type = info.get("op_type")
            if info_op_type not in ("Income", "Outcome"):
                continue
            code = info.get("pnl_code")
            if code != "REVENUE":
                continue

            value = float(part.get("value") or 0)
            sign = 1 if info_op_type == op_type else -1
            signed = sign * value

            totals[ym] += signed
            by_project[pid][ym] += signed
            if pid not in projects_by_id:
                projects_by_id[pid] = project.get("title") or ""

    return {
        "months": months,
        "totals": totals,
        "projects": dict(by_project),
        "project_names": projects_by_id,
    }


def month_range(anchor: str, months: int) -> list[str]:
    """Список YYYY-MM включая anchor и (months-1) предшествующих."""
    y, m = (int(x) for x in anchor.split("-"))
    out: list[str] = []
    for i in range(months - 1, -1, -1):
        yy = y
        mm = m - i
        while mm <= 0:
            mm += 12
            yy -= 1
        out.append(f"{yy:04d}-{mm:02d}")
    return out
