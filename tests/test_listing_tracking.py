from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service, listing_tracking
from app.models import Base, Listing, ListingLike, ListingObservation, ListingTargetPrice, SearchResult
from app.schemas import ListingCreate

@pytest_asyncio.fixture
async def sessions(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, 'SessionLocal', factory)
    async def no_external(payload, **kwargs): return payload, False, False
    monkeypatch.setattr(listing_service, '_enrich_twogis', no_external)
    yield factory
    await engine.dispose()


def payload(**kwargs):
    return ListingCreate(**({'source':'browser', 'source_url':'https://krisha.kz/a/show/1',
        'title':'Test', 'city':'Астана', 'price_kzt':40000000, 'area_m2':100, 'rooms':4,
        'description':'Продаётся квартира с качественным хорошим ремонтом и документами.',
        'details_extracted':True} | kwargs))

@pytest.mark.asyncio
async def test_real_changes_only_and_partial_does_not_erase(sessions):
    async def save(p): return await listing_service.persist_listing(p, notify=False, refresh_ai=False)
    row = (await save(payload())).row
    await save(payload())
    await save(payload(price_kzt=39000000, description='Короткое описание', details_extracted=False))
    await save(payload(price_kzt=39000000, description='Короткое описание', details_extracted=False))
    async with sessions() as session:
        events = (await session.scalars(select(ListingObservation).where(ListingObservation.listing_id == row.id))).all()
        assert len(events) == 1
        assert events[0].changes == {'price_kzt': {'old':40000000, 'new':39000000}}

@pytest.mark.asyncio
async def test_related_age_target_matches_and_paused(sessions):
    now = datetime.now(UTC)
    async with sessions() as session:
        first = Listing(source_url='https://krisha.kz/a/show/11', title='First', city='Астана', address='Абая 10', floor=2, floors_total=5, rooms=3, area_m2=90, price_kzt=45000000, created_at=now - timedelta(days=2))
        second = Listing(source_url='https://krisha.kz/a/show/12', title='Second', city='Астана', address='Абая 10', floor=2, floors_total=5, rooms=3, area_m2=90, price_kzt=39000000, created_at=now - timedelta(days=6))
        session.add_all([first, second]); await session.flush()
        session.add(ListingTargetPrice(listing_id=first.id, target_price_kzt=40000000, include_similar=True, updated_at=now))
        session.add(SearchResult(url=first.source_url, search_engine='test', query='', title='First', first_seen=now - timedelta(days=3)))
        await session.commit()
        summary = await listing_tracking.tracking_summary(session, first.id)
        assert summary['listing_age_days'] == 3
        assert summary['related_age_days'] == 6 and summary['related_confidence'] == 'probable'
        assert summary['target']['paused'] is True
        assert summary['matching_offers'][0]['listing_id'] == second.id
        session.add(ListingLike(url=first.source_url)); await session.commit()
        assert not (await listing_tracking.tracking_summary(session, first.id))['target']['paused']
        second.source_archived_at = now; await session.commit()
        assert await listing_tracking.target_price_matches(session, first.id) == []

@pytest.mark.asyncio
async def test_target_notification_deduplicates_without_real_network(sessions, monkeypatch):
    from types import SimpleNamespace
    from app import telegram
    from app.models import TelegramNotification
    monkeypatch.setattr(telegram, 'SessionLocal', sessions)
    monkeypatch.setattr(telegram, 'get_settings', lambda: SimpleNamespace(telegram_enabled=True, telegram_bot_token='test', telegram_chat_id='test'))
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'ok':True}
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, *, json):
            calls.append(json)
            return Response()
    monkeypatch.setattr(telegram.httpx, 'AsyncClient', FakeClient)
    async with sessions() as session:
        row = Listing(source_url='https://krisha.kz/a/show/98', title='Test', city='Астана', rooms=2, area_m2=80, price_kzt=20)
        session.add(row); await session.commit(); listing_id = row.id
    args = dict(listing_id=listing_id, offer_id=listing_id, price_kzt=20, target_kzt=30,
                target_updated_at=datetime.now(UTC), source_url='https://krisha.kz/a/show/98', relationship='self')
    assert await telegram.notify_target_price(**args)
    assert not await telegram.notify_target_price(**args)
    assert len(calls) == 1
    async with sessions() as session:
        records = (await session.scalars(select(TelegramNotification))).all()
        assert len(records) == 1 and records[0].status == 'sent'
