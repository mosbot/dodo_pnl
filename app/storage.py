"""SQLite-хранилище для целей (targets) и маппинга категорий."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    target_pct  REAL NOT NULL,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, metric_code)
);

CREATE TABLE IF NOT EXISTS default_targets (
    metric_code TEXT PRIMARY KEY,
    target_pct  REAL NOT NULL,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS category_mapping (
    planfact_category_id TEXT PRIMARY KEY,
    pnl_code             TEXT NOT NULL,
    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Конфиг проектов (активность, отображаемое имя, порядок сортировки).
-- Привязан к PlanFact project_id. Записи появляются лениво на /settings.
-- dodo_unit_uuid — привязка к юниту Dodo IS для автосинка ops-метрик.
CREATE TABLE IF NOT EXISTS projects_config (
    project_id     TEXT PRIMARY KEY,
    is_active      INTEGER NOT NULL DEFAULT 1,
    display_name   TEXT,
    sort_order     INTEGER,
    dodo_unit_uuid TEXT,
    updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Операционные метрики (ручной ввод; позже — импорт из DodoIS).
-- period_month хранится как 'YYYY-MM' (первое число месяца).
CREATE TABLE IF NOT EXISTS ops_metrics (
    project_id              TEXT NOT NULL,
    period_month            TEXT NOT NULL,
    orders_per_courier_h    REAL,
    products_per_h          REAL,
    revenue_per_person_h    REAL,
    late_delivery_certs     INTEGER,
    delivery_orders_count   INTEGER,
    late_delivery_certs_pct REAL,
    updated_at              TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, period_month)
);

-- Цели по ops-метрикам. Глобальные (не на проект). Все ops — floor-таргет
-- (факт должен быть ≥ цели), поэтому колонку direction не храним —
-- зашито в коде через OPS_METRICS_HIGHER_IS_BETTER.
CREATE TABLE IF NOT EXISTS ops_targets (
    metric_code  TEXT PRIMARY KEY,
    target_value REAL NOT NULL,
    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Override ops-таргета на уровне конкретной пиццерии.
-- Если записи нет — используется глобальный ops_targets.
CREATE TABLE IF NOT EXISTS ops_project_targets (
    project_id   TEXT NOT NULL,
    metric_code  TEXT NOT NULL,
    target_value REAL NOT NULL,
    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, metric_code)
);

-- Шаблон статей P&L, импортированный из экспорта ПланФакт.
-- Используется для классификации категорий PlanFact API: при сопоставлении
-- по нормализованному path берём pnl_code отсюда; иначе — fallback на эвристику.
-- parent_id ссылается на pnl_template.id (нет внешнего ключа в SQLite по умолчанию,
-- но логика поддерживается через replace_template_tree()).
CREATE TABLE IF NOT EXISTS pnl_template (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id    INTEGER,
    depth        INTEGER NOT NULL,
    title        TEXT NOT NULL,
    path         TEXT NOT NULL,         -- 'A / B / C', оригинальный регистр
    path_lc      TEXT NOT NULL,         -- то же, lowercased — для match
    is_calc      INTEGER NOT NULL DEFAULT 0,
    is_leaf      INTEGER NOT NULL DEFAULT 0,
    pnl_code     TEXT,                  -- UC | LC | DC | RENT | MARKETING | FRANCHISE | MGMT | OTHER_OPEX | REVENUE | OTHER_INCOME | TAX | INTEREST | DIVIDENDS | NULL
    sort_order   INTEGER NOT NULL,
    updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_targets_project ON targets(project_id);
CREATE INDEX IF NOT EXISTS idx_mapping_code ON category_mapping(pnl_code);
CREATE INDEX IF NOT EXISTS idx_ops_metrics_period ON ops_metrics(period_month);
CREATE INDEX IF NOT EXISTS idx_template_path_lc ON pnl_template(path_lc);
CREATE INDEX IF NOT EXISTS idx_template_parent ON pnl_template(parent_id);
"""

# Дефолты при первом старте (только если ключ отсутствует — не перезатираем)
INITIAL_SETTINGS: dict[str, str] = {
    "include_manager_in_lc": "true",
}
INITIAL_DEFAULT_TARGETS: dict[str, float] = {
    "TC": 0.60,
}
# Ops-метрики: коды, лейблы, единицы, имя поля в ops_metrics, и направление.
# digits — сколько знаков после запятой показывать на UI (0 = округлять до целого).
OPS_METRICS: list[dict] = [
    {
        "code": "ORD_PER_COURIER_H",
        "label": "Заказов на курьера в час",
        "unit": "зак/ч",
        "field": "orders_per_courier_h",
        "direction": "higher",  # actual >= target == ok
        "digits": 2,
    },
    {
        "code": "LATE_CERTS",
        "label": "Сертификаты",
        "unit": "%",
        "field": "late_delivery_certs_pct",  # основная величина — процент
        "count_field": "late_delivery_certs",  # абс. количество (показывается в скобках)
        "direction": "lower",   # actual <= target == ok (чем меньше — тем лучше)
        "digits": 1,
    },
    {
        "code": "PROD_PER_H",
        "label": "Продуктов в час (кухня)",
        "unit": "шт/ч",
        "field": "products_per_h",
        "direction": "higher",
        "digits": 2,
    },
    {
        "code": "REV_PER_PERSON_H",
        "label": "Выручка на человека в час",
        "unit": "₽/ч",
        "field": "revenue_per_person_h",
        "direction": "higher",
        "digits": 0,            # округляем до целого по требованию
    },
]
OPS_METRIC_CODES: list[str] = [m["code"] for m in OPS_METRICS]
OPS_METRIC_FIELD_BY_CODE: dict[str, str] = {m["code"]: m["field"] for m in OPS_METRICS}
# Таргеты по ops-метрикам при первом старте — не задаём, пользователь вобьёт сам.
INITIAL_OPS_TARGETS: dict[str, float] = {}


def _migrate(con: sqlite3.Connection) -> None:
    """Лёгкие миграции — идемпотентные ALTER-ы для уже существующих БД."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(projects_config)").fetchall()}
    if "dodo_unit_uuid" not in cols:
        con.execute("ALTER TABLE projects_config ADD COLUMN dodo_unit_uuid TEXT")

    ops_cols = {r["name"] for r in con.execute("PRAGMA table_info(ops_metrics)").fetchall()}
    if "late_delivery_certs" not in ops_cols:
        con.execute("ALTER TABLE ops_metrics ADD COLUMN late_delivery_certs INTEGER")
    if "delivery_orders_count" not in ops_cols:
        con.execute("ALTER TABLE ops_metrics ADD COLUMN delivery_orders_count INTEGER")
    if "late_delivery_certs_pct" not in ops_cols:
        con.execute("ALTER TABLE ops_metrics ADD COLUMN late_delivery_certs_pct REAL")

    # Шаблонные узлы, которые раньше ошибочно считались расчётными (is_calc=1):
    # «Амортизация», «Проценты по кредитам и займам», «Налог на прибыль (доходы)»,
    # «Дивиденды» — это реальные строки P&L с категориями и оборотами. Перевести
    # их в is_calc=0 и проставить корректный pnl_code, чтобы на дашборде у них
    # появились значения. Идемпотентно: WHERE is_calc=1 AND title IN (...).
    con.execute(
        """
        UPDATE pnl_template
           SET is_calc = 0,
               pnl_code = CASE title
                   WHEN 'Проценты по кредитам и займам' THEN 'INTEREST'
                   WHEN 'Налог на прибыль (доходы)'    THEN 'TAX'
                   WHEN 'Дивиденды'                     THEN 'DIVIDENDS'
                   WHEN 'Амортизация'                   THEN pnl_code
                   ELSE pnl_code
               END
         WHERE is_calc = 1
           AND title IN (
                'Амортизация',
                'Проценты по кредитам и займам',
                'Налог на прибыль (доходы)',
                'Дивиденды'
           )
        """
    )


def init_db() -> None:
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        _migrate(con)
        for k, v in INITIAL_SETTINGS.items():
            con.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
                (k, v),
            )
        for metric, pct in INITIAL_DEFAULT_TARGETS.items():
            con.execute(
                "INSERT OR IGNORE INTO default_targets (metric_code, target_pct) VALUES (?, ?)",
                (metric, pct),
            )
        for metric, val in INITIAL_OPS_TARGETS.items():
            con.execute(
                "INSERT OR IGNORE INTO ops_targets (metric_code, target_value) VALUES (?, ?)",
                (metric, val),
            )


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(settings.database_path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


# --- Targets ---

def list_targets(project_id: str | None = None) -> list[dict]:
    with connect() as con:
        if project_id:
            rows = con.execute(
                "SELECT project_id, metric_code, target_pct FROM targets WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT project_id, metric_code, target_pct FROM targets"
            ).fetchall()
        return [dict(r) for r in rows]


def upsert_target(project_id: str, metric_code: str, target_pct: float) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO targets (project_id, metric_code, target_pct)
            VALUES (?, ?, ?)
            ON CONFLICT(project_id, metric_code)
            DO UPDATE SET target_pct = excluded.target_pct,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (project_id, metric_code, target_pct),
        )


def delete_target(project_id: str, metric_code: str) -> None:
    with connect() as con:
        con.execute(
            "DELETE FROM targets WHERE project_id = ? AND metric_code = ?",
            (project_id, metric_code),
        )


# --- Category mapping ---

def list_mappings() -> dict[str, str]:
    with connect() as con:
        rows = con.execute(
            "SELECT planfact_category_id, pnl_code FROM category_mapping"
        ).fetchall()
        return {r["planfact_category_id"]: r["pnl_code"] for r in rows}


def upsert_mapping(planfact_id: str, pnl_code: str) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO category_mapping (planfact_category_id, pnl_code)
            VALUES (?, ?)
            ON CONFLICT(planfact_category_id)
            DO UPDATE SET pnl_code = excluded.pnl_code,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (planfact_id, pnl_code),
        )


# --- App settings (key/value) ---

def list_settings() -> dict[str, str]:
    with connect() as con:
        rows = con.execute("SELECT key, value FROM app_settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as con:
        row = con.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )


def get_bool_setting(key: str, default: bool = False) -> bool:
    val = get_setting(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on", "y", "t")


# --- Default targets (без привязки к проекту) ---

def list_default_targets() -> dict[str, float]:
    with connect() as con:
        rows = con.execute(
            "SELECT metric_code, target_pct FROM default_targets"
        ).fetchall()
        return {r["metric_code"]: r["target_pct"] for r in rows}


def upsert_default_target(metric_code: str, target_pct: float) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO default_targets (metric_code, target_pct) VALUES (?, ?)
            ON CONFLICT(metric_code) DO UPDATE SET target_pct = excluded.target_pct,
                                                   updated_at = CURRENT_TIMESTAMP
            """,
            (metric_code, target_pct),
        )


def delete_default_target(metric_code: str) -> None:
    with connect() as con:
        con.execute(
            "DELETE FROM default_targets WHERE metric_code = ?", (metric_code,)
        )


# --- Projects config (активность / отображаемое имя / сортировка) ---

def list_projects_config() -> dict[str, dict]:
    """Возвращает только проекты, для которых есть запись в projects_config.
    Проекты без записи считаются активными по умолчанию (is_active=True)."""
    with connect() as con:
        rows = con.execute(
            "SELECT project_id, is_active, display_name, sort_order, dodo_unit_uuid "
            "FROM projects_config"
        ).fetchall()
        return {
            r["project_id"]: {
                "is_active": bool(r["is_active"]),
                "display_name": r["display_name"],
                "sort_order": r["sort_order"],
                "dodo_unit_uuid": r["dodo_unit_uuid"],
            }
            for r in rows
        }


# Уникальный маркер: отличаем «поле не прислали» (не менять) от «прислали пустое»
# (очистить значение).
_UNSET = object()


def upsert_project_config(
    project_id: str,
    *,
    is_active: bool | None = None,
    display_name: str | None = None,
    sort_order: int | None = None,
    dodo_unit_uuid=_UNSET,  # type: ignore[assignment]
) -> None:
    """Патчит поля. None у is_active/sort_order значит «не менять».
    Для display_name/dodo_unit_uuid: None или '' — очистить, отсутствие — не менять
    (через sentinel _UNSET)."""
    with connect() as con:
        row = con.execute(
            "SELECT is_active, display_name, sort_order, dodo_unit_uuid "
            "FROM projects_config WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        cur_active = bool(row["is_active"]) if row else True
        cur_name = row["display_name"] if row else None
        cur_order = row["sort_order"] if row else None
        cur_uuid = row["dodo_unit_uuid"] if row else None

        new_active = cur_active if is_active is None else bool(is_active)
        new_name = cur_name if display_name is None else (display_name or None)
        new_order = cur_order if sort_order is None else sort_order
        if dodo_unit_uuid is _UNSET:
            new_uuid = cur_uuid
        else:
            new_uuid = (dodo_unit_uuid or None)

        con.execute(
            """
            INSERT INTO projects_config (project_id, is_active, display_name,
                                         sort_order, dodo_unit_uuid)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                is_active      = excluded.is_active,
                display_name   = excluded.display_name,
                sort_order     = excluded.sort_order,
                dodo_unit_uuid = excluded.dodo_unit_uuid,
                updated_at     = CURRENT_TIMESTAMP
            """,
            (project_id, 1 if new_active else 0, new_name, new_order, new_uuid),
        )


def get_active_project_ids() -> set[str] | None:
    """Вернуть набор активных project_id, если есть хоть одна явная запись;
    иначе None (= считаем все активными). Это позволяет main-странице
    автоматически скрывать неактивные проекты после первой настройки."""
    cfg = list_projects_config()
    if not cfg:
        return None
    return {pid for pid, c in cfg.items() if c["is_active"]}


# --- Ops metrics (ручной ввод) ---

def list_ops_metrics(
    period_month: str | None = None,
    project_id: str | None = None,
) -> dict:
    """
    period_month = 'YYYY-MM' (обязательный для отображения на карточке).
    Возвращает {project_id: {orders_per_courier_h, products_per_h,
                             revenue_per_person_h, late_delivery_certs}}.
    Если project_id задан — только для него.
    """
    sql = (
        "SELECT project_id, period_month, orders_per_courier_h, "
        "products_per_h, revenue_per_person_h, late_delivery_certs, "
        "delivery_orders_count, late_delivery_certs_pct "
        "FROM ops_metrics WHERE 1=1"
    )
    args: list = []
    if period_month:
        sql += " AND period_month = ?"
        args.append(period_month)
    if project_id:
        sql += " AND project_id = ?"
        args.append(project_id)
    with connect() as con:
        rows = con.execute(sql, args).fetchall()
        out: dict = {}
        for r in rows:
            key = r["project_id"]
            payload = {
                "orders_per_courier_h": r["orders_per_courier_h"],
                "products_per_h": r["products_per_h"],
                "revenue_per_person_h": r["revenue_per_person_h"],
                "late_delivery_certs": r["late_delivery_certs"],
                "delivery_orders_count": r["delivery_orders_count"],
                "late_delivery_certs_pct": r["late_delivery_certs_pct"],
            }
            if period_month is None:
                # Без фильтра по месяцу — возвращаем вложенный словарь
                out.setdefault(key, {})[r["period_month"]] = payload
            else:
                out[key] = payload
        return out


def upsert_ops_metric(
    project_id: str,
    period_month: str,
    *,
    orders_per_courier_h: float | None = None,
    products_per_h: float | None = None,
    revenue_per_person_h: float | None = None,
    late_delivery_certs: int | None = None,
    delivery_orders_count: int | None = None,
    late_delivery_certs_pct: float | None = None,
) -> None:
    """Апсертит запись. None у конкретного поля значит «не менять».

    Если переданы late_delivery_certs и delivery_orders_count, но не передан
    late_delivery_certs_pct — посчитаем процент сами.
    """
    if (
        late_delivery_certs_pct is None
        and late_delivery_certs is not None
        and delivery_orders_count is not None
        and delivery_orders_count > 0
    ):
        late_delivery_certs_pct = (
            float(late_delivery_certs) / float(delivery_orders_count) * 100.0
        )
    with connect() as con:
        row = con.execute(
            "SELECT orders_per_courier_h, products_per_h, revenue_per_person_h, "
            "late_delivery_certs, delivery_orders_count, late_delivery_certs_pct "
            "FROM ops_metrics WHERE project_id = ? AND period_month = ?",
            (project_id, period_month),
        ).fetchone()
        cur_o = row["orders_per_courier_h"] if row else None
        cur_p = row["products_per_h"] if row else None
        cur_r = row["revenue_per_person_h"] if row else None
        cur_c = row["late_delivery_certs"] if row else None
        cur_d = row["delivery_orders_count"] if row else None
        cur_pct = row["late_delivery_certs_pct"] if row else None

        new_o = cur_o if orders_per_courier_h is None else orders_per_courier_h
        new_p = cur_p if products_per_h is None else products_per_h
        new_r = cur_r if revenue_per_person_h is None else revenue_per_person_h
        new_c = cur_c if late_delivery_certs is None else int(late_delivery_certs)
        new_d = cur_d if delivery_orders_count is None else int(delivery_orders_count)
        new_pct = cur_pct if late_delivery_certs_pct is None else float(late_delivery_certs_pct)

        con.execute(
            """
            INSERT INTO ops_metrics (project_id, period_month, orders_per_courier_h,
                                     products_per_h, revenue_per_person_h,
                                     late_delivery_certs, delivery_orders_count,
                                     late_delivery_certs_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, period_month) DO UPDATE SET
                orders_per_courier_h    = excluded.orders_per_courier_h,
                products_per_h          = excluded.products_per_h,
                revenue_per_person_h    = excluded.revenue_per_person_h,
                late_delivery_certs     = excluded.late_delivery_certs,
                delivery_orders_count   = excluded.delivery_orders_count,
                late_delivery_certs_pct = excluded.late_delivery_certs_pct,
                updated_at              = CURRENT_TIMESTAMP
            """,
            (project_id, period_month, new_o, new_p, new_r, new_c, new_d, new_pct),
        )


def delete_ops_metric(project_id: str, period_month: str) -> None:
    with connect() as con:
        con.execute(
            "DELETE FROM ops_metrics WHERE project_id = ? AND period_month = ?",
            (project_id, period_month),
        )


def list_ops_metrics_months(project_id: str | None = None) -> list[str]:
    """Какие месяцы заполнены — для подсказок в UI."""
    with connect() as con:
        if project_id:
            rows = con.execute(
                "SELECT DISTINCT period_month FROM ops_metrics "
                "WHERE project_id = ? ORDER BY period_month DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT DISTINCT period_month FROM ops_metrics ORDER BY period_month DESC"
            ).fetchall()
        return [r["period_month"] for r in rows]


# --- Ops targets ---

def list_ops_targets() -> dict[str, float]:
    with connect() as con:
        rows = con.execute(
            "SELECT metric_code, target_value FROM ops_targets"
        ).fetchall()
        return {r["metric_code"]: r["target_value"] for r in rows}


def upsert_ops_target(metric_code: str, target_value: float) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO ops_targets (metric_code, target_value) VALUES (?, ?)
            ON CONFLICT(metric_code) DO UPDATE SET
                target_value = excluded.target_value,
                updated_at   = CURRENT_TIMESTAMP
            """,
            (metric_code, target_value),
        )


def delete_ops_target(metric_code: str) -> None:
    with connect() as con:
        con.execute(
            "DELETE FROM ops_targets WHERE metric_code = ?", (metric_code,)
        )


# --- Ops project targets (override per pizzeria) ---

def list_ops_project_targets(
    project_id: str | None = None,
) -> list[dict]:
    """Возвращает список {project_id, metric_code, target_value}."""
    with connect() as con:
        if project_id:
            rows = con.execute(
                "SELECT project_id, metric_code, target_value FROM ops_project_targets "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT project_id, metric_code, target_value FROM ops_project_targets"
            ).fetchall()
        return [dict(r) for r in rows]


def ops_project_targets_map() -> dict[str, dict[str, float]]:
    """{project_id: {metric_code: target_value}} — удобно для быстрого lookup."""
    out: dict[str, dict[str, float]] = {}
    for r in list_ops_project_targets():
        out.setdefault(r["project_id"], {})[r["metric_code"]] = r["target_value"]
    return out


def upsert_ops_project_target(
    project_id: str, metric_code: str, target_value: float
) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT INTO ops_project_targets (project_id, metric_code, target_value)
            VALUES (?, ?, ?)
            ON CONFLICT(project_id, metric_code)
            DO UPDATE SET target_value = excluded.target_value,
                          updated_at   = CURRENT_TIMESTAMP
            """,
            (project_id, metric_code, target_value),
        )


def delete_ops_project_target(project_id: str, metric_code: str) -> None:
    with connect() as con:
        con.execute(
            "DELETE FROM ops_project_targets WHERE project_id = ? AND metric_code = ?",
            (project_id, metric_code),
        )


def effective_ops_target(
    project_id: str, metric_code: str,
    overrides: dict[str, dict[str, float]] | None = None,
    defaults: dict[str, float] | None = None,
) -> float | None:
    """Вернуть таргет для (project_id, metric_code): override > default > None."""
    if overrides is None:
        overrides = ops_project_targets_map()
    if defaults is None:
        defaults = list_ops_targets()
    per = overrides.get(project_id, {})
    if metric_code in per:
        return per[metric_code]
    return defaults.get(metric_code)


# --- PnL template (импорт из экспорта ПланФакт) ---


def list_template_nodes() -> list[dict]:
    """Возвращает все узлы шаблона в порядке sort_order."""
    with connect() as con:
        rows = con.execute(
            "SELECT id, parent_id, depth, title, path, path_lc, is_calc, is_leaf, "
            "pnl_code, sort_order FROM pnl_template ORDER BY sort_order"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "parent_id": r["parent_id"],
                "depth": r["depth"],
                "title": r["title"],
                # Возвращаем path списком — так же как у парсера превью.
                # В колонке хранится строка "A / B / C", разрезаем по " / ".
                "path": [s for s in (r["path"] or "").split(" / ") if s],
                "path_lc": r["path_lc"],
                "is_calc": bool(r["is_calc"]),
                "is_leaf": bool(r["is_leaf"]),
                "pnl_code": r["pnl_code"],
                "sort_order": r["sort_order"],
            }
            for r in rows
        ]


def template_is_empty() -> bool:
    with connect() as con:
        row = con.execute("SELECT COUNT(1) AS c FROM pnl_template").fetchone()
        return (row["c"] or 0) == 0


def template_path_to_code() -> dict[str, str]:
    """Map нормализованного path → pnl_code (только для не-расчётных строк
    с заданным кодом). Используется в _build_category_index() для override.
    """
    with connect() as con:
        rows = con.execute(
            "SELECT path_lc, pnl_code FROM pnl_template "
            "WHERE is_calc = 0 AND pnl_code IS NOT NULL AND pnl_code != ''"
        ).fetchall()
        return {r["path_lc"]: r["pnl_code"] for r in rows}


def template_leaf_title_to_code() -> dict[str, str]:
    """Map: последний сегмент пути (lowercased) → pnl_code.
    Fallback, если нет точного совпадения по полному пути."""
    out: dict[str, str] = {}
    for n in list_template_nodes():
        if n["is_calc"] or not n["pnl_code"]:
            continue
        leaf = (n["path_lc"].split(" / ") or [""])[-1].strip()
        if leaf:
            out[leaf] = n["pnl_code"]
    return out


def replace_template_tree(nodes: list[dict]) -> int:
    """Полностью заменить шаблон. На вход — плоский список узлов из
    planfact_export.parse_pnl_export()['nodes'].

    Каждый узел должен содержать: title, depth, parent_idx (None или индекс
    в этом же массиве), path (list[str]), is_calc, is_leaf, pnl_code, sort_order.

    parent_idx преобразуется в parent_id (auto id из БД). Возвращает количество вставленных строк.
    """
    with connect() as con:
        con.execute("DELETE FROM pnl_template")
        idx_to_id: dict[int, int] = {}
        for i, n in enumerate(nodes):
            parent_id = idx_to_id.get(n["parent_idx"]) if n.get("parent_idx") is not None else None
            path_str = " / ".join(n["path"])
            cur = con.execute(
                """
                INSERT INTO pnl_template
                  (parent_id, depth, title, path, path_lc, is_calc, is_leaf, pnl_code, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parent_id,
                    int(n["depth"]),
                    n["title"],
                    path_str,
                    path_str.lower(),
                    1 if n.get("is_calc") else 0,
                    1 if n.get("is_leaf") else 0,
                    (n.get("pnl_code") or None),
                    int(n.get("sort_order") or (i + 1)),
                ),
            )
            idx_to_id[i] = cur.lastrowid
        return len(nodes)


def update_template_node_code(node_id: int, pnl_code: str | None) -> bool:
    """Поправить pnl_code конкретного узла. Возвращает True, если строка нашлась."""
    code = pnl_code or None
    with connect() as con:
        cur = con.execute(
            "UPDATE pnl_template SET pnl_code = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (code, node_id),
        )
        return cur.rowcount > 0


def clear_template() -> None:
    with connect() as con:
        con.execute("DELETE FROM pnl_template")
