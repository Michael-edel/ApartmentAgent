import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service, search_agent
from app.cities import city_matches, city_slug
from app.krisha_search import build_krisha_search_url
from app.models import Base, SearchPreference, SearchResult
from app.schemas import ListingCreate


def test_city_identity_and_direct_urls(monkeypatch):
    assert city_slug("Алма-Ата") == "almaty"
    assert city_matches("almaty", None, "Алматы")
    assert not city_matches("astana", None, "Алматы")
    assert city_matches("astana", None, None)
    for city in ("astana", "almaty", "shymkent", "karaganda", "pavlodar"):
        monkeypatch.setattr(search_agent.settings, "search_city", city)
        assert f"/kvartiry/{city}/" in build_krisha_search_url()


@pytest.mark.asyncio
async def test_search_city_persists_and_legacy_defaults_to_astana(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'city.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(search_agent, "SessionLocal", sessions)
    monkeypatch.setattr(search_agent.settings, "search_city", "astana")
    async with sessions() as session:
        session.add(SearchPreference(id=1, rooms=3))
        await session.commit()
    await search_agent.load_search_preference()
    assert search_agent.settings.search_city == "astana"
    await search_agent.save_search_preference(None, city="almaty")
    assert search_agent.settings.search_city == "almaty"
    monkeypatch.setattr(search_agent.settings, "search_city", "astana")
    await search_agent.load_search_preference()
    assert search_agent.settings.search_city == "almaty"
    async with sessions() as session:
        preference = await session.get(SearchPreference, 1)
        assert (preference.city, preference.rooms) == ("almaty", 3)
    await engine.dispose()


@pytest.mark.asyncio
async def test_unconfirmed_import_city_uses_search_or_preserves_saved_city(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, 'SessionLocal', sessions)
    url = 'https://krisha.kz/a/show/123'
    async with sessions() as session:
        session.add(SearchResult(url=url, city='almaty', title='Квартира', query='', search_engine='krisha_direct'))
        await session.commit()

    async def save(**changes):
        payload = ListingCreate(source='browser', source_url=url, title='Квартира',
                                price_kzt=40_000_000, rooms=3, area_m2=80, **changes)
        return (await listing_service.persist_listing(payload, notify=False, refresh_ai=False)).row

    first = await save(city='Астана', city_observed=False, latitude=51.128, longitude=71.43)
    assert (first.city, first.latitude, first.longitude) == ('Алматы', None, None)
    await save(city='Алматы', city_observed=True, latitude=43.24, longitude=76.89)
    legacy = await save(city='Астана')
    assert (legacy.city, legacy.latitude, legacy.longitude) == ('Алматы', 43.24, 76.89)
    corrected = await save(city='Астана', city_observed=True)
    assert (corrected.city, corrected.latitude, corrected.longitude) == ('Астана', None, None)
    await engine.dispose()
