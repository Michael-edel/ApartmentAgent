"""Read-only export of all saved listings; excludes private workflow and files."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Listing

router = APIRouter(prefix='/api/v1/listings', tags=['export'])

_FIELDS = (
    ('id', 'ID'), ('title', 'Название'), ('source_url', 'Ссылка'), ('source', 'Источник'),
    ('city', 'Город'), ('district', 'Район'), ('address', 'Адрес'),
    ('residential_complex', 'Жилой комплекс'), ('market_type', 'Тип рынка'),
    ('rooms', 'Комнат'), ('area_m2', 'Площадь м²'), ('price_kzt', 'Цена ₸'),
    ('floor', 'Этаж'), ('floors_total', 'Этажей в доме'), ('building_year', 'Год постройки'),
    ('bathrooms_count', 'Санузлов'), ('bathrooms_count_is_lower_bound', 'Санузлов не менее'),
    ('seller_type', 'Тип продавца'), ('mortgage_supported', 'Ипотека'), ('is_pledged', 'В залоге'),
    ('created_at', 'Добавлено'), ('last_imported_at', 'Обновлено из источника'),
    ('sold_at', 'Отмечено проданным'), ('source_archived_at', 'Источник архив'),
    ('source_unavailable_at', 'Источник недоступен'),
)


def safe_csv_cell(value):
    """Keep text inert in spreadsheet programs, including prefixed whitespace."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Да' if value else 'Нет'
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    leading = value[:len(value) - len(stripped)]
    if any(char in leading for char in ('\t', '\r', '\n')) or stripped[:1] in ('=', '+', '-', '@'):
        return "'" + value
    return value


def _public_url(value: object) -> str:
    try:
        parsed = urlsplit(str(value or ''))
        if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname:
            return ''
        host = parsed.hostname.lower()
        if ':' in host:
            host = f'[{host}]'
        if parsed.port is not None:
            host += f':{parsed.port}'
        return urlunsplit((parsed.scheme.lower(), host, parsed.path, '', ''))
    except ValueError:
        return ''


@router.get('/export')
async def export_listings(format: Literal['csv', 'json'] = 'csv'):
    async with SessionLocal() as session:
        columns = [getattr(Listing, key) for key, _ in _FIELDS]
        rows = (await session.execute(select(*columns).order_by(Listing.id))).all()
        records = [{key: _public_url(getattr(row, key)) if key == 'source_url' else getattr(row, key) for key, _ in _FIELDS} for row in rows]
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
    if format == 'json':
        data = [{key: value.isoformat() if isinstance(value, (datetime, date)) else value for key, value in record.items()} for record in records]
        return JSONResponse({'scope': 'all_saved_listings', 'items': data}, headers={**headers, 'Content-Disposition': 'attachment; filename="apartment-agent-listings.json"'})
    out = io.StringIO(newline='')
    writer = csv.writer(out, lineterminator='\r\n')
    writer.writerow(label for _, label in _FIELDS)
    writer.writerows([safe_csv_cell(record[key]) for key, _ in _FIELDS] for record in records)
    return Response(out.getvalue().encode('utf-8-sig'), media_type='text/csv; charset=utf-8',
                    headers={**headers, 'Content-Disposition': 'attachment; filename="apartment-agent-listings.csv"'})
