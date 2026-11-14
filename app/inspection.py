"""Local, private inspection notes and attachments for saved listings."""
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ForeignKey, Integer, JSON, LargeBinary, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.database import SessionLocal
from app.models import Base, Listing

router = APIRouter(prefix='/api/v1/listings', tags=['inspection'])
MAX_FILE = 5 * 1024 * 1024
MAX_FILES = 10
CHECKS = frozenset(('meters', 'stairway', 'yard', 'parking', 'window_view', 'documents',
                    'extract', 'plumbing', 'electricity', 'heating', 'ventilation', 'windows',
                    'walls', 'ceiling', 'noise', 'water_pressure', 'elevator', 'internet'))
CATEGORIES = frozenset(('meters', 'stairway', 'yard', 'parking', 'window_view', 'documents', 'extract', 'other'))


class Inspection(Base):
    __tablename__ = 'listing_inspections'
    listing_id: Mapped[int] = mapped_column(ForeignKey('listings.id', ondelete='CASCADE'), primary_key=True)
    checks: Mapped[dict] = mapped_column(JSON, default=dict)
    seller_questions: Mapped[str] = mapped_column(Text, default='')
    notes: Mapped[str] = mapped_column(Text, default='')
    outcome: Mapped[str] = mapped_column(String(20), default='undecided')


class InspectionAttachment(Base):
    __tablename__ = 'listing_inspection_attachments'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey('listings.id', ondelete='CASCADE'), index=True)
    category: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(180))
    media_type: Mapped[str] = mapped_column(String(30))
    size: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)


class InspectionBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    checks: dict[str, bool] = Field(default_factory=dict, max_length=len(CHECKS))
    seller_questions: str = Field(default='', max_length=4000)
    notes: str = Field(default='', max_length=4000)
    outcome: Literal['undecided', 'suitable', 'needs_review', 'unsuitable'] = 'undecided'


def _attachment(row):
    return {'id': row.id, 'category': row.category, 'name': row.name, 'size': row.size, 'media_type': row.media_type}


async def _exists(session, listing_id):
    if await session.get(Listing, listing_id) is None:
        raise HTTPException(404, 'Квартира не найдена')


@router.get('/{listing_id}/inspection')
async def get_inspection(listing_id: int):
    async with SessionLocal() as session:
        await _exists(session, listing_id)
        row = await session.get(Inspection, listing_id)
        attachments = (await session.execute(select(InspectionAttachment.id, InspectionAttachment.category, InspectionAttachment.name, InspectionAttachment.size, InspectionAttachment.media_type).where(InspectionAttachment.listing_id == listing_id).order_by(InspectionAttachment.id))).all()
        return {'checks': row.checks if row else {}, 'seller_questions': row.seller_questions if row else '',
                'notes': row.notes if row else '', 'outcome': row.outcome if row else 'undecided',
                'attachments': [_attachment(a) for a in attachments]}


@router.put('/{listing_id}/inspection')
async def put_inspection(listing_id: int, payload: InspectionBody):
    if not set(payload.checks).issubset(CHECKS):
        raise HTTPException(422, 'Неизвестный пункт осмотра')
    async with SessionLocal() as session, session.begin():
        await _exists(session, listing_id)
        row = await session.get(Inspection, listing_id)
        if row is None:
            row = Inspection(listing_id=listing_id)
            session.add(row)
        row.checks, row.seller_questions, row.notes, row.outcome = payload.checks, payload.seller_questions, payload.notes, payload.outcome
    return {'saved': True}


def _type(data: bytes) -> str | None:
    if data.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP': return 'image/webp'
    if data.startswith(b'%PDF-'): return 'application/pdf'
    return None


@router.post('/{listing_id}/inspection/attachments', status_code=201)
async def add_attachment(listing_id: int, request: Request, category: str = Query(max_length=30), name: str = Query(min_length=1, max_length=180)):
    if category not in CATEGORIES:
        raise HTTPException(422, 'Неизвестный раздел вложения')
    clean_name = name.replace('\\', '/').split('/')[-1].strip()
    if not clean_name or any(ord(c) < 32 for c in clean_name):
        raise HTTPException(422, 'Некорректное имя файла')
    declared_length = request.headers.get('content-length', '')
    if declared_length.isdigit() and int(declared_length) > MAX_FILE:
        raise HTTPException(413, 'Файл больше 5 МБ')
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_FILE:
            raise HTTPException(413, 'Файл больше 5 МБ')
    media_type = _type(content)
    if not media_type:
        raise HTTPException(415, 'Разрешены JPEG, PNG, WebP и PDF')
    async with SessionLocal() as session, session.begin():
        await _exists(session, listing_id)
        count = await session.scalar(select(func.count()).select_from(InspectionAttachment).where(InspectionAttachment.listing_id == listing_id))
        if count >= MAX_FILES:
            raise HTTPException(409, 'Можно прикрепить не больше 10 файлов')
        row = InspectionAttachment(listing_id=listing_id, category=category, name=clean_name, media_type=media_type, size=len(content), data=bytes(content))
        session.add(row)
        await session.flush()
        return _attachment(row)


@router.get('/{listing_id}/inspection/attachments/{attachment_id}')
async def download_attachment(listing_id: int, attachment_id: int):
    async with SessionLocal() as session:
        row = await session.get(InspectionAttachment, attachment_id)
        if row is None or row.listing_id != listing_id:
            raise HTTPException(404, 'Вложение не найдено')
        return Response(row.data, media_type='application/octet-stream', headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(row.name, safe='')}", 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})


@router.delete('/{listing_id}/inspection/attachments/{attachment_id}', status_code=204)
async def delete_attachment(listing_id: int, attachment_id: int):
    async with SessionLocal() as session, session.begin():
        row = await session.get(InspectionAttachment, attachment_id)
        if row is None or row.listing_id != listing_id:
            raise HTTPException(404, 'Вложение не найдено')
        await session.delete(row)
    return Response(status_code=204)
