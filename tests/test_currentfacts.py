import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service, main, search_agent
from app.models import Base, SearchResult
from app.schemas import ListingCreate
from app.search_providers import SearchItem


@pytest.mark.asyncio
@pytest.mark.parametrize('price,area,rooms', [
    (55_000_000, 98.6, 4),
    (45_000_000, 135, 4),
    (45_000_000, 98.6, 3),
])
async def test_imported_facts_override_cached_discovery_and_remain_outside_results(
    monkeypatch, price, area, rooms
):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    for module in (listing_service, search_agent, main):
        monkeypatch.setattr(module, 'SessionLocal', sessions)
    for settings in (main.settings, search_agent.settings):
        for key, value in dict(target_rooms=4, min_area_m2=80, max_area_m2=120,
                               max_price_kzt=50_000_000, min_bathrooms=2).items():
            monkeypatch.setattr(settings, key, value)
    url = 'https://krisha.kz/a/show/123'
    stale = SearchItem(url=url, title='4-комнатная квартира · 98.6 м² · 6/9 этаж',
                       snippet='45 000 000 ₸, 2 санузла')
    async with sessions() as session:
        session.add(SearchResult(url=url, title=stale.title, snippet=stale.snippet,
                                 query='test', search_engine='test', status='new:normal'))
        await session.commit()
    payload = ListingCreate(source='browser', source_url=url, title='Квартира', rooms=rooms,
                            area_m2=area, price_kzt=price, floor=6, floors_total=9,
                            bathrooms_count=2, mortgage_supported=False)
    saved = await listing_service.persist_listing(payload, notify=False, refresh_ai=False)
    async with sessions() as session:
        row = await session.get(SearchResult, 1)
        assert row.imported_listing_id == saved.row.id
        assert row.bathrooms_count == 2 and row.mortgage_supported is False
        assert not search_agent._row_matches_basic(row)
        assert not search_agent._row_matches_target(row)
    assert await main.search_results() == []
    await search_agent._save_items('test', 'test', [stale])
    async with sessions() as session:
        row = await session.get(SearchResult, 1)
        assert f'{price} ₸' in row.title
        assert f'{area:g} м²' in row.title
        assert f'{rooms}-комнатная' in row.title
        assert '6/9 этаж' in row.title
        assert not search_agent._row_matches_target(row)
    assert await main.search_results() == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_unimported_discovery_still_updates(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(search_agent, 'SessionLocal', sessions)
    for key, value in dict(target_rooms=4, min_area_m2=80, max_area_m2=120,
                           max_price_kzt=50_000_000).items():
        monkeypatch.setattr(search_agent.settings, key, value)
    url = 'https://krisha.kz/a/show/456'
    async with sessions() as session:
        session.add(SearchResult(url=url, title='4-комнатная квартира 90 м²',
                                 snippet='30000000 ₸', query='test', search_engine='test'))
        await session.commit()
    item = SearchItem(url=url, title='4-комнатная квартира 95 м²', snippet='40000000 ₸')
    await search_agent._save_items('test', 'test', [item])
    async with sessions() as session:
        row = await session.get(SearchResult, 1)
        assert row.title == item.title and row.snippet == item.snippet
    await engine.dispose()
