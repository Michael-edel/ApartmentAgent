from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class ListingImportRequest(BaseModel):
    source_url: HttpUrl


class ListingCreate(BaseModel):
    source: str = Field(default="manual", max_length=50)
    source_url: HttpUrl
    title: str = Field(min_length=3, max_length=300)
    city: str = "Астана"
    district: str | None = None
    residential_complex: str | None = None
    price_kzt: int = Field(gt=0)
    area_m2: float = Field(gt=0)
    rooms: int = Field(gt=0)
    floor: int | None = Field(default=None, gt=0)
    floors_total: int | None = Field(default=None, gt=0)
    building_year: int | None = Field(default=None, ge=1900, le=2100)
    building_type: str | None = None
    is_full_two_room: bool = True
    mortgage_supported: bool | None = None

    @model_validator(mode="after")
    def validate_floor(self) -> "ListingCreate":
        if self.floor and self.floors_total and self.floor > self.floors_total:
            raise ValueError("floor cannot exceed floors_total")
        return self


class ListingAssessment(BaseModel):
    price_per_m2: int
    score: int = Field(ge=0, le=100)
    verdict: Literal["ПОКУПАТЬ", "СМОТРЕТЬ", "НЕ РЕКОМЕНДУЮ"]
    reasons: list[str]


class ListingResponse(ListingCreate):
    id: int
    created_at: datetime
    assessment: ListingAssessment
