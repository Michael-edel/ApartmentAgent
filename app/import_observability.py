"""Local import journal and durable, per-source retry state.

Only status metadata and field names are stored, never payloads or credentials.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import JSON, DateTime, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.database import SessionLocal
from app.models import Base

Source = Literal['krisha', 'olx']
Status = Literal['success', 'blocked', 'error', 'skipped']
_BLOCK_CODES = frozenset({403, 429, 468})
_MAX_DELAY_SECONDS = 3600
_BASE_DELAY_SECONDS = 60
_EXTENSION_RECENT_SECONDS = 150
_VERIFICATION_MESSAGE = 'Источник запросил проверку браузера'
_MAX_EVENTS_PER_SOURCE = 5000
_FIELDS = frozenset({'title', 'city', 'district', 'address', 'price_kzt', 'area_m2', 'rooms',
                     'floor', 'floors_total', 'building_year', 'building_type', 'description',
                     'seller_type', 'seller_name', 'market_type', 'mortgage_supported', 'is_pledged',
                     'bathrooms_count', 'latitude', 'longitude', 'photo_urls', 'property_details',
                     'repair_condition', 'residential_complex'})
_METHODS = frozenset({'search', 'auto', 'browser', 'manual', 'check', 'queue', 'import'})
_lock = asyncio.Lock()
_request_locks = {source: asyncio.Lock() for source in ('krisha', 'olx')}
_logger = logging.getLogger(__name__)


class ImportEvent(Base):
    __tablename__ = 'import_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    url: Mapped[str] = mapped_column(Text, default='')
    method: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BrowserPresence(Base):
    __tablename__ = 'browser_presence'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _safe_url(source: Source, value: str | None) -> str:
    """Allow only public source URLs and no query, fragment or userinfo."""
    try:
        parsed = urlsplit(value or '')
        hosts = {'krisha': {'krisha.kz', 'www.krisha.kz'}, 'olx': {'olx.kz', 'www.olx.kz'}}
        if (parsed.scheme != 'https' or (parsed.hostname or '').lower() not in hosts[source]
                or parsed.username is not None or parsed.password is not None or parsed.port is not None):
            return ''
        path = parsed.path[:500]
        if not re.fullmatch(r'/[a-zA-Z0-9/_\-.]*', path):
            return ''
        return f'https://{parsed.hostname.lower()}{path}'
    except (ValueError, TypeError):
        return ''


def _event_error(status: str, http_status: int | None, error: str | None) -> str | None:
    # Never persist raw exceptions: they can include tokens, headers or payloads.
    if status == 'blocked' and error == 'verification':
        return _VERIFICATION_MESSAGE
    if http_status in _BLOCK_CODES:
        return f'Источник ограничил доступ (HTTP {http_status})'
    if status == 'blocked':
        return 'Источник ограничил доступ или запросил проверку'
    if status == 'error' or error:
        return 'Ошибка получения или обработки объявления'
    return None


async def _cooldown(session, source: Source, now: datetime) -> dict:
    events = (await session.scalars(select(ImportEvent).where(
        ImportEvent.source == source, ImportEvent.method.not_in(('browser', 'queue')),
    ).order_by(ImportEvent.id.desc()).limit(100))).all()
    blocked = []
    recovered = set()
    for event in events:
        channel = 'search' if event.method == 'search' else 'detail'
        if event.status == 'success':
            recovered.add(channel)
        elif channel not in recovered and (event.status == 'blocked' or event.http_status in _BLOCK_CODES):
            blocked.append(event)
    last = blocked[0] if blocked else None
    until = (_utc(last.observed_at) + timedelta(seconds=min(_MAX_DELAY_SECONDS,
             _BASE_DELAY_SECONDS * (2 ** min(len(blocked) - 1, 6))))) if last else None
    wait = max(0, math.ceil((until-now).total_seconds())) if until else 0
    return {'source':source, 'blocked_count':len(blocked), 'retry_at':until if wait else None,
            'wait_seconds':wait, 'in_cooldown':bool(wait)}


async def source_cooldown(source: Source) -> dict:
    if source not in ('krisha', 'olx'):
        raise ValueError('Unknown source')
    async with SessionLocal() as session:
        return await _cooldown(session, source, _now())


async def record_import(source: Source, url: str | None, method: str, status: Status, *,
                        fields=(), error: str | None = None, http_status: int | None = None) -> dict:
    if source not in ('krisha', 'olx') or status not in ('success', 'blocked', 'error', 'skipped'):
        raise ValueError('Invalid source or status')
    method = method if method in _METHODS else 'import'
    code = http_status if isinstance(http_status, int) and not isinstance(http_status, bool) and 100 <= http_status <= 599 else None
    # A verified blocking HTTP status always takes precedence over reported status.
    status = 'blocked' if code in _BLOCK_CODES else status
    safe_fields = sorted({name for name in fields if isinstance(name, str) and name in _FIELDS})[:30]
    async with _lock, SessionLocal() as session:
        row = ImportEvent(source=source, url=_safe_url(source, url), method=method, status=status,
                          fields=safe_fields, error=_event_error(status, code, error), http_status=code, observed_at=_now())
        session.add(row)
        await session.flush()
        # Bound storage independently for each source without touching cards.
        stale = (await session.scalars(select(ImportEvent.id).where(ImportEvent.source == source)
                 .order_by(ImportEvent.id.desc()).offset(_MAX_EVENTS_PER_SOURCE))).all()
        if stale:
            from sqlalchemy import delete
            await session.execute(delete(ImportEvent).where(ImportEvent.id.in_(stale)))
        await session.commit()
        return await _cooldown(session, source, _now())


class SourceCoolingDown(RuntimeError):
    pass


async def observe(source: Source, url: str | None, method: str, status: Status, **kwargs) -> None:
    try:
        await record_import(source, url, method, status, **kwargs)
    except Exception:
        _logger.warning("Could not write the %s import journal", source)


async def observe_browser_presence(version: str | None) -> None:
    if not re.fullmatch(r'[0-9]{1,5}(?:\.[0-9]{1,5}){1,3}', version or ''):
        return
    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        async with SessionLocal() as session, session.begin():
            insert = pg_insert if session.get_bind().dialect.name == 'postgresql' else sqlite_insert
            statement = insert(BrowserPresence).values(id=1, version=version, observed_at=_now())
            await session.execute(statement.on_conflict_do_update(index_elements=['id'], set_={
                'version': statement.excluded.version, 'observed_at': statement.excluded.observed_at,
            }))
    except Exception:
        _logger.warning("Could not record browser extension activity")


async def observed_fetch(source: Source, url: str | None, method: str, fetch, *args, **kwargs):
    async with _request_locks[source]:
        return await _observe_request(source, url, method, fetch, *args, **kwargs)


async def _observe_request(source: Source, url: str | None, method: str, fetch, *args, **kwargs):
    try:
        state = await source_cooldown(source)
    except Exception:
        _logger.warning("Could not read the %s import journal", source)
    else:
        if state['in_cooldown']:
            raise SourceCoolingDown(f"Источник временно ограничил запросы. Повтор возможен через {state['wait_seconds']} с.")
    try:
        result = await fetch(*args, **kwargs)
    except Exception as exc:
        message = str(exc).lower()
        verification = any(term in message for term in ('проверку безопасности', 'проверку браузера', 'captcha'))
        match = re.search(r'\bhttp\s+(\d{3})\b', message)
        code = int(match[1]) if match else None
        if type(exc).__name__ in {'ListingArchived', 'ListingUnavailable'}:
            state = 'skipped'
        elif (code in _BLOCK_CODES or type(exc).__name__ in {'KrishaSearchBlocked', 'OlxSearchBlocked'}
              or verification):
            state = 'blocked'
        else:
            state = 'error'
        await observe(source, url, method, state, http_status=code, error='verification' if verification else None)
        raise
    fields = result.model_dump(exclude_none=True) if hasattr(result, 'model_dump') else ()
    await observe(source, url, method, 'success', fields=fields)
    return result


router = APIRouter(prefix='/api/v1/import-observability', tags=['import-observability'])


@router.get('')
async def import_observability_status() -> dict:
    async with SessionLocal() as session:
        result = {}
        for source in ('krisha', 'olx'):
            state = await _cooldown(session, source, _now())
            rows = (await session.execute(select(ImportEvent.status, func.count(ImportEvent.id))
                    .where(ImportEvent.source == source).group_by(ImportEvent.status))).all()
            state['counts'] = dict(rows)
            state['browser_verifications'] = await session.scalar(select(func.count(ImportEvent.id)).where(
                ImportEvent.source == source, ImportEvent.error == _VERIFICATION_MESSAGE,
            ))
            latest = await session.scalar(select(ImportEvent).where(ImportEvent.source == source)
                                         .order_by(ImportEvent.id.desc()).limit(1))
            state['last_status'] = latest.status if latest else None
            state['last_observed_at'] = latest.observed_at if latest else None
            result[source] = state
        browser = await session.scalar(select(func.max(ImportEvent.observed_at))
                                       .where(ImportEvent.method.in_(('browser', 'queue'))))
        presence = await session.get(BrowserPresence, 1)
        return {'sources':result, 'last_browser_activity_at':browser,
                'browser_version':presence.version if presence else None,
                'browser_connected':bool(presence and
                    (_now() - _utc(presence.observed_at)).total_seconds() <= _EXTENSION_RECENT_SECONDS)}


@router.get('/events')
async def import_observability_events(source: Source | None = None, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    async with SessionLocal() as session:
        query = select(ImportEvent).order_by(ImportEvent.id.desc()).limit(limit)
        if source:
            query = query.where(ImportEvent.source == source)
        rows = (await session.scalars(query)).all()
        return {'items':[{'id':row.id, 'source':row.source, 'url':row.url, 'method':row.method,
                          'status':row.status, 'fields':row.fields, 'error':row.error,
                          'http_status':row.http_status, 'observed_at':row.observed_at} for row in rows]}
