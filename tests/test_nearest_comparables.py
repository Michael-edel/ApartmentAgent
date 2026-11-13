from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main
from app.market_comparison import compare_saved_listings
from app.models import Base, Listing


NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)


def listing(index, **fields):
    values = dict(id=index, source_url=f'https://krisha.kz/a/show/{index}', city='Астана',
                  district='Есиль', title=f'Квартира {index}', address=f'Улица, {index}',
                  residential_complex='ЖК Central Park', rooms=3, price_kzt=40_000_000,
                  area_m2=100.0, latitude=51.1283, longitude=71.4306,
                  last_imported_at=NOW - timedelta(days=1))
    values.update(fields)
    return SimpleNamespace(**values)


def test_comparables_use_recent_distinct_saved_cards_with_separate_groups():
    target = listing(1, address='Улица 1', price_kzt=35_000_000)
    same = [listing(i, latitude=51.129, residential_complex='central park', area_m2=80)
            for i in range(2, 14)]
    nearby = listing(20, residential_complex='Другой ЖК', latitude=51.1295, area_m2=120)
    rows = [target, *same, nearby,
            listing(21, residential_complex='Другой ЖК', address='Улица, 1', latitude=51.129),
            listing(29, residential_complex='Другой ЖК', address=None, latitude=target.latitude),
            listing(22, residential_complex='Central Park', latitude=51.15),
            listing(23, rooms=4), listing(24, area_m2=120.1),
            listing(25, last_imported_at=NOW - timedelta(days=91)),
            listing(26, city='Алматы'),
            listing(27, source_url='https://www.krisha.kz/a/show/2?old=1',
                    last_imported_at=NOW - timedelta(days=10)),
            listing(28, source_url='https://www.krisha.kz/a/show/1?old=1')]
    assert 'comparables' not in compare_saved_listings(target, rows, now=NOW)
    result = compare_saved_listings(target, rows, now=NOW, include_comparables=True)['comparables']
    assert result['same_complex']['status'] == 'ok'
    assert result['same_complex']['count'] == len(result['same_complex']['items']) == 12
    assert result['nearby']['count'] == 1
    item = result['nearby']['items'][0]
    assert item['source_url'].endswith('/20') and 0 < item['distance_m'] < 1000
    assert item['price_per_m2'] == round(40_000_000 / 120)


def test_missing_location_is_unknown_not_a_zero_result():
    target = listing(1, residential_complex=None, latitude=None, longitude=None)
    result = compare_saved_listings(target, [listing(2)], now=NOW, include_comparables=True)['comparables']
    assert result['same_complex']['status'] == 'complex_unknown'
    assert result['nearby']['status'] == 'coordinates_unknown'
    assert result['nearby']['items'] == []


@pytest.mark.asyncio
async def test_comparison_endpoint_adds_comparables_only_when_requested(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    now = datetime.now(UTC)
    async with sessions() as session:
        for index in (1, 2):
            session.add(Listing(source='browser', source_url=f'https://krisha.kz/a/show/{index}',
                                title=f'Квартира {index}', city='Астана', district='Есиль', rooms=3,
                                area_m2=100, price_kzt=40_000_000, residential_complex='ЖК Central Park',
                                latitude=51.1283, longitude=71.4306, last_imported_at=now))
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        assert 'comparables' not in (await client.get('/api/v1/listings/1/comparison')).json()
        response = await client.get('/api/v1/listings/1/comparison', params={'include_comparables': 'true'})
        assert response.status_code == 200
        assert response.json()['comparables']['same_complex']['count'] == 1
    await engine.dispose()
