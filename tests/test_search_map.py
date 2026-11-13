from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main, search_agent
from app.models import Base, Listing, SearchResult


@pytest.mark.asyncio
async def test_map_includes_all_matches_without_fabricating_pins(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    for settings in (main.settings, search_agent.settings):
        for key, value in {'target_rooms': 3, 'min_area_m2': 70, 'max_area_m2': None,
                           'min_price_kzt': 20_000_000, 'max_price_kzt': 50_000_000,
                           'min_bathrooms': 0, 'exclude_pledged': True}.items():
            monkeypatch.setattr(settings, key, value)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    async with sessions() as session:
        for suffix, latitude, longitude, price, role in [
            ('1', 51.1283, 71.4306, 40_000_000, 'Хозяин недвижимости'),
            ('2', 51.161, 71.434, 40_000_000, 'Риелтор'),
            ('3', None, None, 40_000_000, 'Хозяин недвижимости'),
            ('4', 51.1283, 71.4306, 60_000_000, 'Хозяин недвижимости'),
        ]:
            session.add(Listing(source='browser', source_url=f'https://krisha.kz/a/show/{suffix}',
                                title=f'Квартира {suffix}', city='Астана', rooms=3, area_m2=80,
                                price_kzt=price, latitude=latitude, longitude=longitude, seller_type=role))
        # A stale search fragment must not override saved price or duplicate a saved pin.
        session.add_all([
            SearchResult(url='https://www.krisha.kz/a/show/1?x=1', title='3-комнатная квартира 80 м² 30000000 ₸',
                         query='test', search_engine='test', first_seen=now),
            SearchResult(url='https://krisha.kz/a/show/4', title='3-комнатная квартира 80 м² 30000000 ₸',
                         query='test', search_engine='test', first_seen=now),
        ])
        # >200 matching search-only records have no pins and must still be counted.
        for index in range(205):
            session.add(SearchResult(url=f'https://krisha.kz/a/show/{index + 1000}',
                                     title='3-комнатная квартира 80 м² 30000000 ₸', query='test',
                                     search_engine='test', first_seen=now + timedelta(seconds=index)))
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.get('/api/v1/search/map')
        assert response.status_code == 200
        data = response.json()
        assert {item['source_url'].split('/')[-1] for item in data['items']} == {'1', '2'}
        assert data['without_coordinates'] == 206
        left = (await client.get('/api/v1/search/map', params={'bank': 'left'})).json()
        assert [item['source_url'].split('/')[-1] for item in left['items']] == ['1']
        assert left['without_coordinates'] == 206
        owner = (await client.get('/api/v1/search/map', params={'seller': 'owner'})).json()
        assert [item['source_url'].split('/')[-1] for item in owner['items']] == ['1']
        assert owner['without_coordinates'] == 1
    await engine.dispose()
