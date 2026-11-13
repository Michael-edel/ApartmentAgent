from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main, search_agent
from app.models import Base, Listing, SearchResult
from app.scoring import assess_listing
from app.schemas import ListingCreate
from app.search_analysis import analyze_search_result


def test_left_bank_bonus_is_applied_before_cap_for_saved_and_found():
    data = ListingCreate(source_url='https://krisha.kz/a/show/123', title='Тест',
                         rooms=2, area_m2=60, price_kzt=30_000_000,
                         floor=1, floors_total=9, latitude=51.1283, longitude=71.4306)
    from app.config import Settings
    settings = Settings(target_rooms=2, min_bathrooms=0)
    assessment = assess_listing(data, settings)
    assert assessment.score == 90 and any('Левый берег' in reason for reason in assessment.reasons)
    assert assess_listing(data.model_copy(update={'city': 'Караганда'}), settings).score == 45
    query = dict(max_price=50_000_000, min_area=50, max_area=100, target_rooms=2)
    text = '2-комнатная квартира 60 м²'
    assert analyze_search_result(text, None, **query)['score'] == 80
    assert analyze_search_result(text, None, **query, river_bank='left')['score'] == 85
    assert analyze_search_result(text, None, **query, river_bank='right')['score'] == 80
    full = analyze_search_result(text, '30000000 ₸', **query, river_bank='left')
    assert full['score'] == 100


@pytest.mark.asyncio
async def test_left_bank_view_filters_before_limit_and_matches_priority_counter(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    for module in (main, search_agent):
        monkeypatch.setattr(module, 'SessionLocal', sessions)
    for settings in (main.settings, search_agent.settings):
        for key, value in {'target_rooms': 2, 'min_area_m2': 50, 'max_area_m2': 100,
                           'max_price_kzt': 50_000_000, 'min_bathrooms': 0,
                           'exclude_pledged': False}.items():
            monkeypatch.setattr(settings, key, value)
    when = datetime(2026, 1, 1, tzinfo=UTC)
    async with sessions() as session:
        listing = Listing(source_url='https://krisha.kz/a/show/1', source='manual',
                          title='Квартира', city='Астана', rooms=2, area_m2=60,
                          price_kzt=30_000_000, latitude=51.1283, longitude=71.4306)
        session.add(listing)
        await session.flush()
        session.add(SearchResult(url=listing.source_url, search_engine='test', query='test',
                                 title='2-комнатная квартира 60 м²', status='new:normal',
                                 imported_listing_id=listing.id, first_seen=when))
        for index in range(205):
            session.add(SearchResult(url=f'https://krisha.kz/a/show/{index + 1000}',
                                     search_engine='test', query='test',
                                     title='2-комнатная квартира 60 м²', status='new:normal',
                                     first_seen=when + timedelta(seconds=index + 1)))
        await session.commit()
    left = await main.search_results(limit=1, bank='left')
    assert len(left) == 1 and left[0]['url'].endswith('/1')
    assert left[0]['river_bank'] == 'left' and left[0]['analysis']['score'] == 85
    assert left[0]['priority'] == 'urgent'
    assert (await search_agent.get_search_status())['urgent_results'] == 1
    all_rows = await main.search_results(limit=1)
    assert all_rows[0]['river_bank'] == 'unknown' and all_rows[0]['analysis']['score'] == 80
    saved = await main.list_listings()
    assert saved[0].river_bank == 'left'
    await engine.dispose()
