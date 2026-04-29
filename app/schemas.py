"""Pydantic-схемы для входных параметров API.

Аннотации намеренно в старом стиле (Optional/List вместо X | Y и list[X]),
чтобы работало на Python 3.9 (Apple Command Line Tools).
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class TargetIn(BaseModel):
    project_id: str
    metric_code: str = Field(..., description="UC | LC | DC | TC | RENT | MARKETING")
    target_pct: float = Field(..., description="Целевой % от выручки (0.35 = 35%)")


class DefaultTargetIn(BaseModel):
    metric_code: str = Field(..., description="UC | LC | DC | TC | RENT | MARKETING")
    target_pct: float


class MetricIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=32,
                      description="UC | LC | DC | TC | EBITDA | ... — стабильный код")
    label: str = Field(..., min_length=1, max_length=128)
    formula: str = Field(..., min_length=1)
    is_target: bool = False
    format: str = Field(default="pct", description="pct | rub | x")
    sort_order: int = 0
    min_visibility_level: int = Field(
        default=0, ge=0, le=100,
        description="Минимальный visibility_level юзера для показа",
    )


class FormulaPreviewIn(BaseModel):
    formula: str


class SettingIn(BaseModel):
    key: str = Field(..., description="Имя настройки, напр. include_manager_in_lc")
    value: str = Field(..., description="Строковое значение; для bool — 'true'/'false'")


class PnLQuery(BaseModel):
    date_start: str  # YYYY-MM-DD
    date_end: str
    project_ids: Optional[List[str]] = None
    compare_start: Optional[str] = None
    compare_end: Optional[str] = None


class ProjectConfigIn(BaseModel):
    """Патч-модель конфига проекта.
    Поля опциональны — меняем только то, что пришло."""
    project_id: str
    is_active: Optional[bool] = None
    display_name: Optional[str] = Field(
        None, description="'' очистит кастомное имя"
    )
    sort_order: Optional[int] = None
    dodo_unit_uuid: Optional[str] = Field(
        None, description="UUID юнита Dodo IS для автосинка ops-метрик. '' очистит."
    )

    # Используем model_fields_set, чтобы отличать «поле не прислали» (=не менять)
    # от «прислали пустое» (=очистить). FastAPI/Pydantic по умолчанию подставляют
    # None, если ключа нет в JSON — теряется семантика.


class OpsMetricIn(BaseModel):
    """Запись по одному проекту за один месяц.
    Поля метрик необязательны — None значит «не менять»."""
    project_id: str
    period_month: str = Field(..., description="'YYYY-MM' — напр. '2026-04'")
    orders_per_courier_h: Optional[float] = None
    products_per_h: Optional[float] = None
    revenue_per_person_h: Optional[float] = None


class OpsTargetIn(BaseModel):
    metric_code: str = Field(
        ..., description="ORD_PER_COURIER_H | PROD_PER_H | REV_PER_PERSON_H"
    )
    target_value: float = Field(
        ..., description="Целевое значение (floor, факт ≥ цели)"
    )


class OpsProjectTargetIn(BaseModel):
    """Override ops-таргета на уровне пиццерии."""
    project_id: str
    metric_code: str = Field(
        ..., description="ORD_PER_COURIER_H | PROD_PER_H | REV_PER_PERSON_H"
    )
    target_value: float


class TemplateNodeCodeIn(BaseModel):
    """PATCH одного узла шаблона: поменять pnl_code.
    pnl_code = '' или null → очистить (узел станет неклассифицированным)."""
    pnl_code: Optional[str] = Field(
        None,
        description="UC | LC | DC | RENT | MARKETING | FRANCHISE | MGMT | "
                    "OTHER_OPEX | REVENUE | OTHER_INCOME | TAX | INTEREST | DIVIDENDS",
    )


class TemplateSaveIn(BaseModel):
    """Сохранить шаблон целиком (после превью). На входе — список узлов в
    том же формате, что и preview из /api/template/preview."""
    nodes: List[dict]
