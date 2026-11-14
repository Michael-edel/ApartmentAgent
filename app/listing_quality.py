"""Transparent listing quality, confirmed-group view and audited corrections."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column, selectinload

from app.cities import is_city_coordinate_pair
from app.database import SessionLocal
from app.models import Base, Listing, PriceSnapshot
from app.property_duplicates import confirmed_component, load_duplicate_decisions
from app.seller import seller_category


class ListingCorrection(Base):
    __tablename__ = 'listing_corrections'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey('listings.id', ondelete='CASCADE'), index=True)
    field: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


_FIELD_LABELS = {
    'city':'Город', 'address':'Адрес', 'price_kzt':'Цена', 'area_m2':'Площадь', 'rooms':'Комнаты',
    'floor':'Этаж', 'floors_total':'Этажность', 'building_year':'Год постройки',
    'bathrooms_count':'Количество санузлов', 'repair_condition':'Ремонт',
    'mortgage_supported':'Ипотека', 'seller_type':'Автор объявления',
    'is_pledged':'Залог', 'market_type':'Тип рынка', 'description':'Описание',
}
_MERGE_FIELDS = tuple(_FIELD_LABELS)


def _known(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _location_precision(row: Listing) -> str:
    if is_city_coordinate_pair(row.city, row.latitude, row.longitude):
        return 'listing_coordinates'
    if is_city_coordinate_pair(row.city, row.approximate_latitude, row.approximate_longitude):
        return 'approximate'
    return 'unknown'


def _evidence(row: Listing) -> dict:
    return {
        'bathrooms': {'value':row.bathrooms_count, 'lower_bound':bool(row.bathrooms_count_is_lower_bound),
                      'field':(row.property_details or {}).get('Санузел')},
        'repair': {'value':row.repair_condition, 'source_kind':row.repair_source_kind, 'evidence':row.repair_evidence},
        'mortgage': {'value':row.mortgage_supported, 'source_kind':row.mortgage_source_kind, 'evidence':row.mortgage_evidence},
        'seller': {'value':row.seller_type, 'category':seller_category(row.seller_type)},
    }


def _quality(row: Listing) -> dict:
    fields = {key:_known(getattr(row, key)) for key in _FIELD_LABELS}
    fields['seller_type'] = seller_category(row.seller_type) != 'unknown'
    total = len(fields)
    flags = [key for key, known in fields.items() if not known]
    precision = _location_precision(row)
    photos = len([url for url in (row.photo_urls or []) if isinstance(url, str) and url.strip()])
    if precision != 'listing_coordinates': flags.append('location_' + precision)
    if not photos: flags.append('photos_missing')
    if row.bathrooms_count is not None and row.bathrooms_count_is_lower_bound: flags.append('bathrooms_lower_bound')
    if row.rooms >= 4 and row.area_m2 < 70:
        flags.append('compact_for_room_count')
    if row.market_type == 'new' and row.building_year and row.building_year < datetime.now(UTC).year - 10:
        flags.append('older_new_building')
    if row.bathrooms_count and row.bathrooms_count >= 2 and not photos:
        flags.append('multiple_bathrooms_no_photos')
    return {'listing_id':row.id, 'completeness_percent':round(sum(fields.values()) / total * 100),
            'fields':fields, 'field_labels':_FIELD_LABELS, 'photo_count':photos,
            'location_precision':precision, 'evidence':_evidence(row), 'review_flags':flags}


async def _listing(session, listing_id: int) -> Listing:
    row = await session.scalar(select(Listing).where(Listing.id == listing_id)
                               .options(selectinload(Listing.prices)))
    if row is None:
        raise HTTPException(404, 'Карточка не найдена')
    return row


async def _merged(session, listing_id: int) -> dict:
    selected = await _listing(session, listing_id)
    decisions = await load_duplicate_decisions(session)
    ids = confirmed_component(listing_id, decisions)
    rows = list((await session.scalars(select(Listing).where(Listing.id.in_(ids))
                 .options(selectinload(Listing.prices)))).all())
    rows.sort(key=lambda row: (row.id != listing_id, row.id))
    fields = {}
    conflicts = []
    for name in _MERGE_FIELDS:
        present = [row for row in rows if _known(getattr(row, name))]
        if not present: continue
        first = present[0]
        candidates = [{'listing_id':row.id, 'source_url':row.source_url, 'value':getattr(row, name)} for row in present]
        distinct = {str(item['value']).strip().casefold() for item in candidates}
        if len(distinct) > 1: conflicts.append(name)
        fields[name] = {'value':getattr(first,name), 'listing_id':first.id, 'source_url':first.source_url,
                        'alternatives':candidates if len(distinct) > 1 else []}
    offers = [{'listing_id':row.id, 'source_url':row.source_url, 'source':row.source,
               'price_kzt':row.price_kzt, 'photo_urls':row.photo_urls or [],
               'price_history':[{'price_kzt':p.price_kzt, 'price_per_m2':p.price_per_m2,
                                 'observed_at':p.observed_at} for p in sorted(row.prices, key=lambda p:p.observed_at)],
               'evidence':_evidence(row)} for row in rows]
    return {'listing_id':selected.id, 'group_type':'confirmed', 'fields':fields,
            'conflicts':conflicts, 'offers':offers}


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: Literal['bathrooms_count', 'repair_condition', 'mortgage_supported', 'is_pledged', 'seller_type']
    value: Any
    reason: str = Field(min_length=3, max_length=500)

    @field_validator('reason')
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError('Укажите причину исправления')
        return value


def _validate_correction(field: str, value: Any) -> Any:
    if value is None: return None
    if field in {'mortgage_supported','is_pledged'}:
        if type(value) is not bool: raise HTTPException(422, 'Нужно указать да, нет или неизвестно')
        return value
    if field == 'bathrooms_count':
        if type(value) is not int or not 1 <= value <= 20: raise HTTPException(422, 'Укажите число санузлов от 1 до 20')
        return value
    if not isinstance(value, str) or not value.strip(): raise HTTPException(422, 'Укажите значение или неизвестно')
    value = value.strip()
    if field == 'seller_type':
        if len(value) > 100 or seller_category(value) == 'unknown': raise HTTPException(422, 'Укажите известный тип продавца')
    elif len(value) > 500:
        raise HTTPException(422, 'Слишком длинное значение')
    return value


# Shared with the import writer: coordinates SQLite transactions inside one process.
listing_write_lock = asyncio.Lock()

router = APIRouter(prefix='/api/v1/listings', tags=['listing-quality'])


@router.get('/{listing_id}/quality')
async def listing_quality(listing_id: int) -> dict:
    async with SessionLocal() as session:
        return _quality(await _listing(session, listing_id))


@router.get('/{listing_id}/merged-view')
async def listing_merged_view(listing_id: int) -> dict:
    async with SessionLocal() as session:
        return await _merged(session, listing_id)


@router.get('/{listing_id}/corrections')
async def correction_history(listing_id: int, limit: int = Query(50, ge=1, le=200)) -> dict:
    async with SessionLocal() as session:
        await _listing(session, listing_id)
        rows = (await session.scalars(select(ListingCorrection).where(ListingCorrection.listing_id == listing_id)
                .order_by(ListingCorrection.id.desc()).limit(limit))).all()
        return {'items':[{'id':r.id, 'field':r.field, 'old_value':r.old_value, 'new_value':r.new_value,
                          'reason':r.reason, 'observed_at':r.observed_at} for r in rows]}


@router.put('/{listing_id}/corrections')
async def correct_listing(listing_id: int, request: CorrectionRequest) -> dict:
    value = _validate_correction(request.field, request.value)
    async with listing_write_lock, SessionLocal() as session, session.begin():
        row = await session.scalar(select(Listing).where(Listing.id == listing_id).with_for_update())
        if row is None:
            raise HTTPException(404, 'Карточка не найдена')
        old = getattr(row, request.field)
        if old == value:
            previous = await session.scalar(select(ListingCorrection.id).where(
                ListingCorrection.listing_id == listing_id, ListingCorrection.field == request.field).limit(1))
            if previous is not None:
                return {'listing_id':listing_id, 'changed':False, 'field':request.field, 'value':value}
        setattr(row, request.field, value)
        if request.field == 'bathrooms_count': row.bathrooms_count_is_lower_bound = False
        if request.field == 'repair_condition':
            row.repair_source_kind, row.repair_evidence = ('manual' if value is not None else None), None
        if request.field == 'mortgage_supported':
            row.mortgage_source_kind, row.mortgage_evidence = ('manual' if value is not None else None), None
        # Keep connected discovery facts consistent, including deliberate clearing.
        from app.listing_service import _matching_search_results, sync_search_result_facts
        for result in await _matching_search_results(session, row.source_url):
            sync_search_result_facts(result, row, clear_unknown_mortgage=request.field == 'mortgage_supported')
            if request.field == 'bathrooms_count':
                result.bathrooms_count = value
                result.bathrooms_count_is_lower_bound = False
            if request.field == 'is_pledged': result.is_pledged = value
            if request.field == 'seller_type': result.seller_type = value
        event = ListingCorrection(listing_id=listing_id, field=request.field, old_value=old,
                                  new_value=value, reason=request.reason, observed_at=datetime.now(UTC))
        session.add(event)
        await session.flush()
        return {'listing_id':listing_id, 'changed':True, 'field':request.field, 'value':value, 'correction_id':event.id}
