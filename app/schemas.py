from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class ListingImportRequest(BaseModel):
    source_url: HttpUrl


class ListingCreate(BaseModel):
    source: str = Field(default="manual", max_length=50)
    source_url: HttpUrl
    title: str = Field(min_length=3, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    city: str = "Астана"
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
    krisha_district_price_per_m2: int | None = Field(default=None, gt=0, le=1_000_000_000)
    krisha_city_price_per_m2: int | None = Field(default=None, gt=0, le=1_000_000_000)
    krisha_comparison_district: str | None = Field(default=None, max_length=150)
    photo_urls: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("photo_urls")
    @classmethod
    def validate_photo_urls(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for raw_url in value:
            url = str(raw_url).strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            if url not in result:
                result.append(url)
        return result

    @model_validator(mode="after")
    def validate_floor(self) -> "ListingCreate":
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
    created_at: datetime
    assessment: ListingAssessment
    ai_analysis: dict[str, object] | None = None
    price_history: list[PriceSnapshotResponse] = Field(default_factory=list)
    krisha_comparison_observed_at: datetime | None = None
    krisha_comparison_source: str | None = None
    complex_description_observed_at: datetime | None = None
