"""Saved search profiles; activation applies criteria as one locked update."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.cities import SEARCH_CITIES, SearchCity
from app.database import SessionLocal
from app.models import SearchPreference, SearchProfile
from app.search_conditions import SearchConditions
from app.market_type import MarketFilter

router = APIRouter(prefix='/api/v1/search/profiles', tags=['search-profiles'])


class ProfileCriteria(BaseModel):
    city: SearchCity = 'astana'
    market_type: MarketFilter = 'all'
    seller: Literal['all', 'owner'] = 'all'
    rooms: int = Field(default=2, ge=2, le=4)
    min_price_kzt: int | None = Field(default=None, gt=0)
    max_price_kzt: int = Field(gt=0)
    min_area_m2: float = Field(gt=0, allow_inf_nan=False)
    max_area_m2: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    min_bathrooms: int = Field(default=0, ge=0, le=20)
    exclude_pledged: bool = True
    conditions: SearchConditions = Field(default_factory=SearchConditions)

    @model_validator(mode='after')
    def ranges(self):
        if self.min_price_kzt is not None and self.min_price_kzt > self.max_price_kzt:
            raise ValueError('Минимальная цена выше максимальной')
        if self.max_area_m2 is not None and self.min_area_m2 > self.max_area_m2:
            raise ValueError('Минимальная площадь выше максимальной')
        if self.rooms not in (2, 3, 4) or self.min_bathrooms not in (0, 2):
            raise ValueError('Недопустимые комнаты или санузлы')
        return self


class ProfileBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = False
    criteria: ProfileCriteria

    @model_validator(mode='after')
    def nonblank(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('Укажите название профиля')
        return self


def profile_dict(row):
    return {'id': row.id, 'name': row.name, 'enabled': row.enabled, 'criteria': row.criteria}


@router.get('')
async def list_profiles():
    async with SessionLocal() as session:
        rows = (await session.scalars(select(SearchProfile).order_by(SearchProfile.id))).all()
        pref = await session.get(SearchPreference, 1)
        visible = [row for row in rows if (row.criteria or {}).get('city', 'astana') in SEARCH_CITIES]
        active = pref.active_profile_id if pref and any(row.id == pref.active_profile_id for row in visible) else None
        return {'items': [profile_dict(row) for row in visible], 'active_profile_id': active}


@router.post('', status_code=201)
async def create_profile(body: ProfileBody):
    async with SessionLocal() as session:
        row = SearchProfile(name=body.name, enabled=body.enabled, criteria=body.criteria.model_dump())
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return profile_dict(row)


@router.put('/{profile_id}')
async def update_profile(profile_id: int, body: ProfileBody):
    async with SessionLocal() as session:
        row = await session.get(SearchProfile, profile_id)
        if row is None:
            raise HTTPException(404, 'Профиль не найден')
        row.name, row.enabled, row.criteria = body.name, body.enabled, body.criteria.model_dump()
        await session.commit()
        return profile_dict(row)


@router.delete('/{profile_id}')
async def delete_profile(profile_id: int):
    from app.search_agent import _search_lock
    async with _search_lock:
        async with SessionLocal() as session:
            row = await session.get(SearchProfile, profile_id)
            if row is None:
                raise HTTPException(404, 'Профиль не найден')
            pref = await session.get(SearchPreference, 1)
            if pref and pref.active_profile_id == profile_id:
                pref.active_profile_id = None
            await session.delete(row)
            await session.commit()
            return {'deleted': True}


@router.post('/{profile_id}/activate')
async def activate_profile(profile_id: int):
    from app.search_agent import _search_lock, save_search_preference
    async with _search_lock:
        async with SessionLocal() as session:
            row = await session.get(SearchProfile, profile_id)
            if row is None:
                raise HTTPException(404, 'Профиль не найден')
            if (row.criteria or {}).get('city', 'astana') not in SEARCH_CITIES:
                raise HTTPException(422, 'Город профиля больше не поддерживается для поиска')
            criteria = ProfileCriteria.model_validate(row.criteria)
        await save_search_preference(criteria.rooms, criteria.max_price_kzt, criteria.min_area_m2,
            criteria.max_area_m2, criteria.min_bathrooms, criteria.exclude_pledged,
            max_area_m2_is_set=True, min_price_kzt=criteria.min_price_kzt, min_price_kzt_is_set=True,
            city=criteria.city, conditions=criteria.conditions.model_dump(), active_profile_id=profile_id,
            market_type=criteria.market_type, seller=criteria.seller)
        return {'active_profile_id': profile_id}
