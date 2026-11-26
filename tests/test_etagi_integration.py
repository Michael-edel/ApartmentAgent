import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import etagi, import_observability, listing_service, main, search_agent
from app.config import get_settings, search_settings_override
from app.models import Base, Listing, SearchResult
from app.schemas import ListingCreate


@pytest.mark.asyncio
async def test_etagi_search_import_and_map_use_the_selected_city(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    for module in (search_agent, main, listing_service, import_observability):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    monkeypatch.setattr(main, "settings", search_agent.settings)
    for key in ("ai_enabled", "twogis_enabled", "telegram_enabled"):
        monkeypatch.setattr(get_settings(), key, False)

    urls = {
        "astana": "https://astana.etagi.com/realty/10101/",
        "ekibastuz": "https://ekibastuz.etagi.com/realty/20202/",
    }
    records = {
        "astana": ("Астана", 51.13, 71.43, 40_000_000),
        "ekibastuz": ("Экибастуз", 51.72, 75.32, 26_000_000),
    }
    requested = []

    async def catalogue(*, city, **criteria):
        requested.append((city, criteria["min_bathrooms_count"]))
        name, latitude, longitude, price = records[city]
        return [ListingCreate(
            source="etagi", source_url=urls[city], title="4-комнатная квартира",
            city=name, price_kzt=price, area_m2=95, rooms=4, bathrooms_count=2,
            seller_type="Риелтор", market_type="secondary", latitude=latitude, longitude=longitude,
        )]

    async def empty_results(query):
        return []

    monkeypatch.setattr(etagi, "search_apartments", catalogue)
    monkeypatch.setitem(search_agent.PROVIDERS, "krisha_direct", empty_results)
    base = get_settings().model_copy(update={
        "search_enabled": True, "search_providers": ["etagi_direct"], "market_type": "all",
        "search_seller": "all", "target_rooms": 4, "min_bathrooms": 2, "min_price_kzt": None,
        "max_price_kzt": 50_000_000, "min_area_m2": 80, "max_area_m2": None,
        "search_conditions": {}, "auto_import_enabled": True,
    })
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        for city, url in urls.items():
            token = search_settings_override.set(base.model_copy(update={"search_city": city}))
            try:
                summary = await search_agent._run_search()
                assert (summary.found, summary.imported, summary.errors, summary.import_errors) == (1, 1, 0, 0)
                response = await client.get("/api/v1/search/map")
                response.raise_for_status()
                assert [(item["source_url"], item["platform"]) for item in response.json()["items"]] == [(url, "etagi")]
                owner_summary = await search_agent._run_search(seller="owner")
                assert "etagi_direct" not in owner_summary.providers
            finally:
                search_settings_override.reset(token)
    async with sessions() as session:
        results = (await session.scalars(select(SearchResult).order_by(SearchResult.id))).all()
        assert [(row.search_engine, row.city, row.import_status) for row in results] == [
            ("etagi_direct", city, "imported") for city in urls
        ]
        listings = (await session.scalars(select(Listing).order_by(Listing.id))).all()
        assert [(row.source_url, row.price_kzt, row.bathrooms_count) for row in listings] == [
            (url, records[city][3], 2) for city, url in urls.items()
        ]
    assert requested == [("astana", 2), ("ekibastuz", 2)]
    await engine.dispose()
