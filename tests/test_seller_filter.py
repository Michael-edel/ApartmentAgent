from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main, search_agent
from app.models import Base, Listing, SearchResult
from app.author import OWNER_FROM_DESCRIPTION


@pytest.mark.asyncio
async def test_seller_filters_before_limit_in_both_lists(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    for settings in (main.settings, search_agent.settings):
        for key, value in {'target_rooms': 2, 'min_area_m2': 50, 'max_area_m2': 100,
                           'max_price_kzt': 50_000_000, 'min_bathrooms': 0,
                           'exclude_pledged': False}.items():
            monkeypatch.setattr(settings, key, value)
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    async with sessions() as session:
        listing = Listing(source_url='https://krisha.kz/a/show/123', source='manual',
                          title='Квартира', city='Астана', rooms=2, area_m2=70,
                          price_kzt=30_000_000, seller_type='Хозяин недвижимости')
        session.add(listing)
        await session.flush()
        session.add(SearchResult(url=listing.source_url, search_engine='test', query='test',
                                 title='2-комнатная квартира 70 м² 30000000 ₸', status='new:normal',
                                 imported_listing_id=listing.id, first_seen=stamp))
        # Older matching owner must remain discoverable behind more than 200 unknowns.
        for index in range(205):
            session.add(SearchResult(url=f'https://krisha.kz/a/show/{1000 + index}', search_engine='test',
                                     query='test', title='2-комнатная квартира 70 м² 30000000 ₸ от хозяина',
                                     status='new:normal', first_seen=stamp + timedelta(seconds=index + 1)))
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.get('/api/v1/search/results', params={'seller': 'owner', 'limit': 1})
        assert response.status_code == 200
        assert len(response.json()) == 1 and response.json()[0]['url'].endswith('/123')
        assert response.json()[0]['seller_category'] == 'owner'
        unknown = (await client.get('/api/v1/search/results', params={'seller': 'unknown', 'limit': 2})).json()
        assert len(unknown) == 2 and all(item['seller_category'] == 'unknown' for item in unknown)
        assert (await client.get('/api/v1/search/results', params={'seller': 'verified_agent'})).json() == []
        assert (await client.get('/api/v1/search/results', params={'seller': 'invalid'})).status_code == 422
        saved = (await client.get('/api/v1/listings', params={'seller': 'owner'})).json()
        assert len(saved) == 1 and saved[0]['seller_category'] == 'owner'
        assert (await client.get('/api/v1/listings', params={'seller': 'unknown'})).json() == []
        assert (await client.get('/api/v1/listings', params={'seller': 'invalid'})).status_code == 422
    await engine.dispose()


@pytest.mark.asyncio
async def test_verified_discovery_role_is_used_only_without_explicit_linked_role(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    for settings in (main.settings, search_agent.settings):
        for key, value in {'target_rooms': 2, 'min_area_m2': 50, 'max_area_m2': 100,
                           'max_price_kzt': 50_000_000, 'min_bathrooms': 0,
                           'exclude_pledged': False}.items():
            monkeypatch.setattr(settings, key, value)
    async with sessions() as session:
        for index, linked_role in enumerate((None, OWNER_FROM_DESCRIPTION, 'Застройщик', 'Риелтор')):
            listing = Listing(source_url=f'https://krisha.kz/a/show/{index + 1}', source='manual',
                              title='Квартира', city='Астана', rooms=2, area_m2=70,
                              price_kzt=30_000_000, seller_type=linked_role)
            session.add(listing)
            await session.flush()
            session.add(SearchResult(url=listing.source_url, search_engine='krisha_direct', query='test',
                                     title='2-комнатная квартира 70 м² 30000000 ₸', status='new:normal',
                                     seller_type='Хозяин недвижимости', imported_listing_id=listing.id))
        session.add(SearchResult(url='https://krisha.kz/a/show/500', search_engine='krisha_direct', query='test',
                                 title='2-комнатная квартира 70 м² 30000000 ₸', status='new:normal',
                                 seller_type='Хозяин недвижимости'))
        await session.commit()
    rows = await main.search_results(limit=20, seller='owner')
    assert {item['url'].split('/')[-1] for item in rows} == {'1', '2', '500'}
    all_rows = await main.search_results(limit=20)
    classified = {item['url'].split('/')[-1]: item['seller_category'] for item in all_rows}
    assert classified['3'] == 'unknown' and classified['4'] == 'realtor'
    await engine.dispose()
