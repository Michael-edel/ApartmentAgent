from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.availability import availability_key
from app.database import SessionLocal
from app.models import Destination, Listing, ListingWorkflow, SearchResult

router = APIRouter(prefix='/api/v1')
Stage = Literal['new', 'call', 'viewing', 'viewed', 'rejected']


class WorkflowPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stage: Stage = 'new'
    note: str = Field(default='', max_length=4000)
    viewing_at: datetime | None = None
    hidden: bool = Field(default=False, strict=True)

    @field_validator('viewing_at')
    @classmethod
    def require_offset(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError('Укажите часовой пояс даты просмотра')
        return value.astimezone(UTC)


class HiddenRequest(BaseModel):
    hidden: bool = Field(strict=True)


class DestinationInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=500)

    @field_validator('name', 'address', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


def workflow_payload(row: ListingWorkflow | None) -> dict:
    if row is None:
        return {'stage': 'new', 'note': '', 'viewing_at': None, 'hidden': False}
    viewing_at = row.viewing_at
    if viewing_at is not None and viewing_at.utcoffset() is None:
        viewing_at = viewing_at.replace(tzinfo=UTC)
    return {'stage': row.stage, 'note': row.note, 'viewing_at': viewing_at, 'hidden': row.hidden}


async def hidden_urls(session: AsyncSession) -> set[str]:
    return set(await session.scalars(select(ListingWorkflow.url).where(ListingWorkflow.hidden.is_(True))))


async def _set_workflow(session: AsyncSession, url: str, changes: dict) -> dict:
    key = availability_key(url)
    if changes:
        insert = pg_insert if session.get_bind().dialect.name == 'postgresql' else sqlite_insert
        await session.execute(insert(ListingWorkflow).values(url=key, **changes).on_conflict_do_update(
            index_elements=['url'], set_=changes,
        ))
    return workflow_payload(await session.get(ListingWorkflow, key))


async def _require_row(session: AsyncSession, model, row_id: int):
    row = await session.get(model, row_id)
    if row is None:
        raise HTTPException(404, 'Объявление не найдено')
    return row


@router.get('/listings/{listing_id}/workflow')
async def get_workflow(listing_id: int) -> dict:
    async with SessionLocal() as session:
        listing = await _require_row(session, Listing, listing_id)
        return workflow_payload(await session.get(ListingWorkflow, availability_key(listing.source_url)))


@router.put('/listings/{listing_id}/workflow')
async def set_workflow(listing_id: int, payload: WorkflowPatch) -> dict:
    async with SessionLocal() as session, session.begin():
        listing = await _require_row(session, Listing, listing_id)
        return await _set_workflow(session, listing.source_url, payload.model_dump(exclude_unset=True))


@router.put('/search/results/{result_id}/hidden')
async def set_found_hidden(result_id: int, payload: HiddenRequest) -> dict:
    async with SessionLocal() as session, session.begin():
        result = await _require_row(session, SearchResult, result_id)
        return await _set_workflow(session, result.url, {'hidden': payload.hidden})


def _destination_payload(row: Destination) -> dict:
    return {'id': row.id, 'name': row.name, 'address': row.address}


@router.get('/destinations')
async def list_destinations() -> list[dict]:
    async with SessionLocal() as session:
        return [_destination_payload(row) for row in await session.scalars(select(Destination).order_by(Destination.id))]


@router.post('/destinations', status_code=201)
async def add_destination(payload: DestinationInput) -> dict:
    async with SessionLocal() as session, session.begin():
        row = Destination(name=payload.name, address=payload.address)
        session.add(row)
        await session.flush()
        return _destination_payload(row)


@router.delete('/destinations/{destination_id}', status_code=204)
async def delete_destination(destination_id: int) -> Response:
    async with SessionLocal() as session, session.begin():
        result = await session.execute(delete(Destination).where(Destination.id == destination_id))
        if result.rowcount == 0:
            raise HTTPException(404, 'Место не найдено')
    return Response(status_code=204)
