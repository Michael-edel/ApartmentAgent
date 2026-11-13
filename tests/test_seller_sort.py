from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main, search_agent
from app.models import Base, Listing, SearchResult


@pytest.mark.asyncio
async def test_seller_sort_uses_linked_listing_before_limit_and_preserves_newest_ties(monkeypatch):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    for settings in (main.settings, search_agent.settings):
        for name, value in {"target_rooms": 2, "min_area_m2": 50, "max_area_m2": 100,
                            "max_price_kzt": 50_000_000, "min_bathrooms": 0,
                            "exclude_pledged": False}.items():
            monkeypatch.setattr(settings, name, value)
    when = datetime(2026, 1, 1, tzinfo=UTC)
    async with sessions() as session:
        for index, seller in enumerate(("Хозяин недвижимости", "Крыша Агент", "Риелтор",
                                        "Хозяин недвижимости", None)):
            listing = Listing(source_url=f"https://krisha.kz/a/show/{index + 1}", source="manual",
                              title="Квартира", city="Астана", rooms=2, area_m2=70,
                              price_kzt=30_000_000, seller_type=seller)
            session.add(listing)
            await session.flush()
            session.add(SearchResult(url=listing.source_url, search_engine="test", query="test",
                                     title="2-комнатная квартира 70 м² 30000000 ₸",
                                     status="new:normal", imported_listing_id=listing.id,
                                     first_seen=when + timedelta(seconds=index)))
        session.add(SearchResult(url="https://krisha.kz/a/show/900", search_engine="test", query="test",
                                 title="2-комнатная квартира 70 м² 30000000 ₸ от хозяина",
                                 status="new:normal", first_seen=when + timedelta(seconds=10)))
        await session.commit()

    items = await main.search_results(limit=10, sort="seller")
    assert [item["seller_category"] for item in items] == [
        "owner", "owner", "verified_agent", "realtor", "unknown", "unknown"
    ]
    assert [item["url"].rsplit("/", 1)[-1] for item in items[:2]] == ["4", "1"]
    assert items[-2]["seller_type"] is None  # An untrusted snippet does not identify seller.
    assert (await main.search_results(limit=1, sort="seller"))[0]["url"].endswith("/4")
    assert (await main.search_results(limit=1))[0]["url"].endswith("/900")
    saved = await main.list_listings()
    assert next(item for item in saved if str(item.source_url).endswith("/2")).seller_category == "verified_agent"
    await engine.dispose()
