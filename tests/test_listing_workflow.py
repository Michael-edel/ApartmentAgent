from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main, workflow
from app.models import Base, Listing, SearchResult


@pytest.mark.asyncio
async def test_notes_hide_duplicates_and_destinations_share_saved_urls(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setattr(workflow, 'SessionLocal', sessions)
    for key, value in {'search_city': 'astana', 'target_rooms': 3, 'min_area_m2': 70,
                       'max_area_m2': None, 'max_price_kzt': 60_000_000, 'min_bathrooms': 0,
                       'search_conditions': {}}.items():
        monkeypatch.setattr(main.settings, key, value)
    title = '4-комнатная квартира · 90 м² · 4/9 этаж · 40 000 000 ₸'
    async with sessions() as session:
        for key, area, house in [(1, 90, 'Абая 1'), (2, 90, 'Абая 1'), (3, 92, 'Абая 3')]:
            session.add(Listing(id=key, source_url=f'https://krisha.kz/a/show/{key}', title=title,
                                city='Астана', address=house, rooms=4, area_m2=area, floor=4, floors_total=9,
                                price_kzt=40_000_000, latitude=51.14, longitude=71.43,
                                last_imported_at=datetime.now(UTC)))
        await session.flush()
        session.add(SearchResult(id=1, url='https://www.krisha.kz/a/show/1?track=yes', query='',
                                 title=title, search_engine='krisha_direct', imported_listing_id=1))
        await session.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        one = '/api/v1/listings/1'
        assert (await client.put(f'{one}/workflow', json={'stage': 'viewing', 'note': 'Уточнить этаж',
                      'viewing_at': '2026-11-16T18:00:00+05:00'})).status_code == 200
        await client.put('/api/v1/search/results/1/hidden', json={'hidden': True})
        state = (await client.get(f'{one}/workflow')).json()
        assert state['stage'] == 'viewing' and state['note'] == 'Уточнить этаж' and state['hidden']
        assert state['viewing_at'].startswith('2026-11-16T13:00:00')
        assert (await client.get('/api/v1/search/results')).json() == []
        assert (await client.get('/api/v1/search/results?include_hidden=true')).json()[0]['hidden']
        pins = (await client.get('/api/v1/search/map')).json()['items']
        assert {item['listing_id'] for item in pins} == {2, 3}
        items = {row['id']: row for row in (await client.get('/api/v1/listings')).json()}
        assert items[1]['workflow_stage'] == 'viewing' and items[2]['hidden_duplicate']

        assert (await client.put(f'{one}/duplicates/2', json={'decision': 'different'})).status_code == 200
        group = (await client.get(f'{one}/duplicates')).json()
        assert [item['listing_id'] for item in group['items']] == [1]
        assert [item['listing_id'] for item in group['rejected']] == [2]
        assert (await client.put(f'{one}/duplicates/3', json={'decision': 'same'})).status_code == 200
        group = (await client.get(f'{one}/duplicates')).json()
        assert group['items'][1]['listing_id'] == 3 and group['items'][1]['evidence']['kind'] == 'confirmed'
        assert (await client.put('/api/v1/listings/2/duplicates/3', json={'decision': 'same'})).status_code == 409
        await client.put(f'{one}/duplicates/3', json={'decision': None})
        assert len((await client.get(f'{one}/duplicates')).json()['items']) == 1
        await client.put(f'{one}/workflow', json={'hidden': False})
        assert (await client.get('/api/v1/search/results')).json()[0]['hidden'] is False

        place = await client.post('/api/v1/destinations', json={'name': 'Школа', 'address': 'Астана, Абая 12'})
        assert place.status_code == 201
        assert (await client.get('/api/v1/destinations')).json() == [place.json()]
        assert (await client.delete(f'/api/v1/destinations/{place.json()["id"]}')).status_code == 204
        assert (await client.get('/api/v1/destinations')).json() == []
    await engine.dispose()
