from datetime import datetime
from html import unescape
import re
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from app.krisha_search import normalize_krisha_import_url
from app.market_type import MarketType


def plain_detail_text(value: str) -> str:
    value = re.sub(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", value, flags=re.I | re.S)
    value = unescape(re.sub(r"<[^>]*>", " ", value))
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)).strip()


class ListingImportRequest(BaseModel):
    source_url: HttpUrl

    @field_validator("source_url", mode="before")
    @classmethod
    def listing_number_or_url(cls, value):
        if isinstance(value, HttpUrl):
            value = str(value)
        if isinstance(value, str):
            normalized = normalize_krisha_import_url(value)
            if normalized:
                return normalized
        raise ValueError("Укажите номер объявления или ссылку https://krisha.kz/a/show/номер")


class ListingCreate(BaseModel):
    source: str = Field(default="manual", max_length=50)
    source_url: HttpUrl
    title: str = Field(min_length=3, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    repair_condition: str | None = Field(default=None, max_length=500)
    repair_source_kind: Literal["field", "description", "manual"] | None = None
    repair_evidence: str | None = Field(default=None, max_length=500)
    mortgage_source_kind: Literal["field", "description", "title", "manual"] | None = None
    mortgage_evidence: str | None = Field(default=None, max_length=500)
    property_details: dict[str, str] = Field(default_factory=dict, max_length=60)
    details_extracted: bool = Field(default=False, exclude=True)
    availability_status: Literal['active', 'archived'] | None = Field(default=None, exclude=True)
    city: str = "Астана"
    market_type: MarketType | None = None
    city_observed: bool | None = Field(default=None, exclude=True)
    district: str | None = None
    residential_complex: str | None = None
    complex_description: str | None = Field(default=None, max_length=5000)
    complex_description_source_url: HttpUrl | None = None
    complex_description_source_kind: Literal['listing', 'developer', 'manual'] | None = None
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    price_kzt: int = Field(gt=0)
    area_m2: float = Field(gt=0)
    rooms: int = Field(gt=0)
    bathrooms_count: int | None = Field(default=None, ge=0, le=20)
    bathrooms_count_is_lower_bound: bool = False
    floor: int | None = Field(default=None, gt=0)
    floors_total: int | None = Field(default=None, gt=0)
    building_year: int | None = Field(default=None, ge=1900, le=2100)
    building_type: str | None = None
    seller_name: str | None = Field(default=None, max_length=200)
    seller_type: str | None = Field(default=None, max_length=100)
    twogis_name: str | None = Field(default=None, max_length=300)
    twogis_rating: float | None = Field(default=None, ge=0, le=5)
    twogis_review_count: int | None = Field(default=None, ge=0)
    twogis_url: str | None = Field(default=None, max_length=500)
    is_full_two_room: bool = True
    mortgage_supported: bool | None = None
    is_pledged: bool | None = None
    krisha_district_price_per_m2: int | None = Field(default=None, gt=0, le=1_000_000_000)
    krisha_city_price_per_m2: int | None = Field(default=None, gt=0, le=1_000_000_000)
    krisha_comparison_district: str | None = Field(default=None, max_length=150)
    photo_urls: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("repair_condition", mode="before")
    @classmethod
    def clean_repair_condition(cls, value):
        return (plain_detail_text(value) or None) if isinstance(value, str) else value

    @field_validator("property_details")
    @classmethod
    def clean_property_details(cls, value: dict[str, str]) -> dict[str, str]:
        result = {}
        for label, detail in value.items():
            label, detail = plain_detail_text(label), plain_detail_text(detail)
            if len(label) > 100 or len(detail) > 1500:
                raise ValueError("Характеристика: название до 100 символов, значение до 1500")
            if label and detail:
                result[label] = detail
        return result

    @field_validator("photo_urls")
    @classmethod
    def validate_photo_urls(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for raw_url in value:
            url = str(raw_url).strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            if re.search(r"^https?://(?:www\.)?krisha\.kz/static/(?:frontend/)?images/empty-photo(?:[-.]|$)", url, re.I):
                continue
            if url not in result:
                result.append(url)
        return result

    @model_validator(mode="after")
    def validate_floor(self) -> "ListingCreate":
        if self.repair_source_kind == "description" and (not self.repair_condition or not self.repair_evidence):
            raise ValueError("Для ремонта из описания нужны значение и цитата")
        if self.mortgage_source_kind in {"description", "title"} and (self.mortgage_supported is None or not self.mortgage_evidence):
            raise ValueError("Для ипотеки из текста нужны значение и цитата")
        if self.complex_description is not None:
            self.complex_description = self.complex_description.strip() or None
        if self.complex_description:
            if self.complex_description_source_kind is None:
                raise ValueError('Укажите источник описания ЖК')
            if self.complex_description_source_kind != 'manual' and self.complex_description_source_url is None:
                raise ValueError('Для описания из объявления или сайта застройщика нужна ссылка')
        elif self.complex_description_source_url or self.complex_description_source_kind:
            raise ValueError('Источник описания ЖК требует самого описания')
        if self.floor and self.floors_total and self.floor > self.floors_total:
            raise ValueError("floor cannot exceed floors_total")
        if (
            self.krisha_district_price_per_m2 is not None
            or self.krisha_city_price_per_m2 is not None
        ) and self.source_url.host not in {"krisha.kz", "www.krisha.kz"}:
            raise ValueError("Krisha comparison requires a krisha.kz source URL")
        return self


class ListingAssessment(BaseModel):
    price_per_m2: int
    score: int = Field(ge=0, le=100)
    verdict: Literal["ПОКУПАТЬ", "СМОТРЕТЬ", "НЕ РЕКОМЕНДУЮ"]
    reasons: list[str]


class PriceSnapshotResponse(BaseModel):
    price_kzt: int
    price_per_m2: int | None = None
    observed_at: datetime
    change_kzt: int | None = None
    change_percent: float | None = None


class ListingResponse(ListingCreate):
    id: int
    hidden: bool = False
    hidden_duplicate: bool = False
    workflow_stage: Literal['new', 'call', 'viewing', 'viewed', 'rejected'] = 'new'
    matches_search_conditions: bool = True
    river_bank: Literal['left', 'right', 'unknown'] = 'unknown'
    liked: bool = False
    availability: Literal['active', 'sold', 'archived', 'unavailable'] = 'active'
    sold_at: datetime | None = None
    source_archived_at: datetime | None = None
    source_unavailable_at: datetime | None = None
    seller_category: Literal['owner', 'verified_agent', 'realtor', 'unknown'] = 'unknown'
    created_at: datetime
    assessment: ListingAssessment
    ai_analysis: dict[str, object] | None = None
    price_history: list[PriceSnapshotResponse] = Field(default_factory=list)
    krisha_comparison_observed_at: datetime | None = None
    krisha_comparison_source: str | None = None
    complex_description_observed_at: datetime | None = None
    details_imported_at: datetime | None = None
