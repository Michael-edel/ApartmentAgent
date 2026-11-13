"""Conservative, explicit buyer requirements shared by results and saved cards."""
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Mode = Literal['off', 'hard', 'preference']


class YearCondition(BaseModel):
    value: int | None = Field(default=None, ge=1900, le=2100)
    mode: Mode = 'off'

    @model_validator(mode='after')
    def require_value(self):
        if self.mode != 'off' and self.value is None:
            raise ValueError('Укажите год постройки')
        return self


class KitchenCondition(BaseModel):
    value: float | None = Field(default=None, gt=0, le=500, allow_inf_nan=False)
    mode: Mode = 'off'

    @model_validator(mode='after')
    def require_value(self):
        if self.mode != 'off' and self.value is None:
            raise ValueError('Укажите площадь кухни')
        return self


class SearchConditions(BaseModel):
    first_floor: Mode = 'off'
    last_floor: Mode = 'off'
    min_build_year: YearCondition = Field(default_factory=YearCondition)
    max_build_year: YearCondition = Field(default_factory=YearCondition)
    min_kitchen_m2: KitchenCondition = Field(default_factory=KitchenCondition)

    @model_validator(mode='after')
    def valid_range(self):
        low, high = self.min_build_year, self.max_build_year
        if low.mode != 'off' and high.mode != 'off' and low.value > high.value:
            raise ValueError('Минимальный год не может превышать максимальный')
        return self


def kitchen_area(property_details: dict | None = None, text: str | None = None) -> float | None:
    candidates = []
    for label, value in (property_details or {}).items():
        if re.search(r'площадь\s+кухни|кухня', str(label), re.I):
            candidates.append(str(value))
    if text:
        match = re.search(r'(?:площадь\s+кухни|кухня)\s*[:—-]?\s*(\d{1,3}(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м)', text, re.I)
        if match:
            candidates.append(match.group(1))
    for value in candidates:
        match = re.match(r'^\s*(\d{1,3}(?:[.,]\d+)?)(?:\s*(?:м²|м2|кв\.?\s*м))?\s*$', value, re.I)
        if match and 0 < float(match.group(1).replace(',', '.')) <= 500:
            return float(match.group(1).replace(',', '.'))
    return None


def evaluate_conditions(conditions, *, floor=None, floors_total=None, building_year=None, kitchen_m2=None):
    spec = conditions if isinstance(conditions, SearchConditions) else SearchConditions.model_validate(conditions or {})
    checks = [
        ('Первый этаж', spec.first_floor, None if floor is None else floor != 1),
        ('Последний этаж', spec.last_floor, None if floor is None or floors_total is None else floor != floors_total),
        ('Минимальный год постройки', spec.min_build_year.mode, None if building_year is None else building_year >= (spec.min_build_year.value or 0)),
        ('Максимальный год постройки', spec.max_build_year.mode, None if building_year is None else building_year <= (spec.max_build_year.value or 9999)),
        ('Минимальная площадь кухни', spec.min_kitchen_m2.mode, None if kitchen_m2 is None else kitchen_m2 >= (spec.min_kitchen_m2.value or 0)),
    ]
    matches, adjustment, reasons, unknown, failed_hard = True, 0, [], [], []
    for label, mode, passed in checks:
        if mode == 'off':
            continue
        if passed is None:
            unknown.append(label)
            if mode == 'hard':
                matches = False
        elif not passed:
            reasons.append(label + ': условие не выполнено')
            if mode == 'hard':
                matches = False
                failed_hard.append(label)
            else:
                adjustment -= 10
        elif label == 'Минимальная площадь кухни' and mode == 'preference':
            adjustment += 5
            reasons.append(label + ': пожелание выполнено (+5)')
    return {'matches_hard': matches, 'score_adjustment': adjustment, 'reasons': reasons, 'unknown': unknown, 'failed_hard': failed_hard}
