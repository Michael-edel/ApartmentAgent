from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main
from app.models import Base, Listing, ListingLike, SearchResult


@pytest.mark.asyncio
async def test_compare_all_liked_deduplicates_and_does_not_filter_by_current_criteria(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setattr(main.settings, 'target_rooms', 4)
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    async with sessions() as session:
        session.add_all([ListingLike(url='https://krisha.kz/a/show/123'),
                         ListingLike(url='https://krisha.kz/a/show/456'),
                         ListingLike(url='https://krisha.kz/a/show/789')])
        session.add(Listing(source='browser', source_url='https://krisha.kz/a/show/123',
                            title='Сохранённая квартира', city='Астана', district='Есиль',
                            address='Тестовая улица', rooms=3, area_m2=75, price_kzt=45_000_000,
                            floor=3, floors_total=9, bathrooms_count=2, repair_condition='Хороший ремонт',
                            mortgage_supported=True, seller_type='Хозяин недвижимости'))
        session.add_all([
            SearchResult(url='https://www.krisha.kz/a/show/123?x=1', title='Устаревшее 2-комнатная 60 м²',
                         snippet='10000000 ₸', query='test', search_engine='test', first_seen=stamp),
            SearchResult(url='https://krisha.kz/a/show/456', title='2-комнатная квартира 60 м² 3/9',
                         snippet='от 30000000 ₸', query='test', search_engine='test',
                         seller_type='Крыша Агент', first_seen=stamp + timedelta(seconds=1)),
            SearchResult(url='https://www.krisha.kz/a/show/456?old=1', title='Старая',
                         query='test', search_engine='test', first_seen=stamp),
        ])
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.get('/api/v1/comparison/liked')
    assert response.status_code == 200
    rows = {row['source_url']: row for row in response.json()}
    assert set(rows) == {'https://krisha.kz/a/show/123', 'https://krisha.kz/a/show/456'}
    saved = rows['https://krisha.kz/a/show/123']
    assert saved['source'] == 'saved' and saved['title'] == 'Сохранённая квартира'
    assert saved['price_kzt'] == 45_000_000 and saved['price_per_m2'] == 600_000
    assert saved['rooms'] == 3 and saved['area_m2'] == 75 and saved['seller_category'] == 'owner'
    found = rows['https://krisha.kz/a/show/456']
    assert found['source'] == 'found' and found['listing_id'] is None
    assert found['price_kzt'] is None and found['price_lower_bound_kzt'] == 30_000_000
    assert found['price_is_lower_bound'] and found['price_per_m2'] is None
    assert found['address'] is None and found['seller_category'] == 'verified_agent'
    await engine.dispose()
