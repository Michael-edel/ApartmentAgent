from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main, search_agent
from app.models import Base, SearchResult


@pytest.mark.asyncio
async def test_search_sorts_before_limit_and_places_unknown_values_last(monkeypatch):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    for settings in (main.settings, search_agent.settings):
        for key, value in {"target_rooms": 2, "min_area_m2": 50, "max_area_m2": 100,
                           "max_price_kzt": 50_000_000, "min_bathrooms": 0,
                           "exclude_pledged": False}.items():
            monkeypatch.setattr(settings, key, value)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async with sessions() as session:
        # The cheapest row is older than the first 200 newest candidates.
        session.add(SearchResult(city='astana', url="https://example.com/old", search_engine="test", query="test",
                                 title="2-комнатная квартира 80 м² 10000000 ₸", status="new:normal",
                                 first_seen=start))
        for number in range(205):
            session.add(SearchResult(city='astana', url=f"https://example.com/{number}", search_engine="test", query="test",
                                     title="2-комнатная квартира 60 м² 30000000 ₸", status="new:normal",
                                     first_seen=start + timedelta(seconds=number + 1)))
        session.add(SearchResult(city='astana', url="https://example.com/unknown", search_engine="test", query="test",
                                 title="2-комнатная квартира 70 м²", status="new:normal",
                                 first_seen=start + timedelta(seconds=300)))
        session.add(SearchResult(city='astana', url="https://example.com/lower", search_engine="test", query="test",
                                 title="2-комнатная квартира 70 м² от 20000000 ₸", status="new:normal",
                                 first_seen=start + timedelta(seconds=301)))
        session.add(SearchResult(city='astana', url="https://example.com/larger", search_engine="test", query="test",
                                 title="2-комнатная квартира 95 м² 35000000 ₸", status="new:normal",
                                 first_seen=start + timedelta(seconds=299)))
        await session.commit()

    assert (await main.search_results(limit=1))[0]["url"].endswith("/lower")
    assert (await main.search_results(limit=1, sort="price_asc"))[0]["url"].endswith("/old")
    assert (await main.search_results(limit=1, sort="price_desc"))[0]["url"].endswith("/larger")
    assert (await main.search_results(limit=1, sort="price_m2_desc"))[0]["url"].endswith("/204")
    assert (await main.search_results(limit=1, sort="area_desc"))[0]["url"].endswith("/larger")
    for sort in ("price_asc", "price_desc", "price_m2_asc", "price_m2_desc"):
        rows = await main.search_results(limit=200, sort=sort)
        assert all(row["analysis"]["price_kzt"] is not None for row in rows)
    # When all candidates fit, unknown and lower-bound prices remain last in newest order.
    async with sessions() as session:
        await session.execute(delete(SearchResult).where(SearchResult.url.like("https://example.com/%"),
                                                         SearchResult.url.not_in([
                                                             "https://example.com/old", "https://example.com/unknown",
                                                             "https://example.com/lower", "https://example.com/0"])))
        await session.commit()
    for sort in ("price_asc", "price_desc", "price_m2_asc", "price_m2_desc"):
        rows = await main.search_results(limit=10, sort=sort)
        assert [row["url"].split("/")[-1] for row in rows[-2:]] == ["lower", "unknown"]
        assert rows[-2]["analysis"]["price_is_lower_bound"] is True
    assert (await main.search_results(limit=1, sort="area_desc"))[0]["url"].endswith("/old")
    await engine.dispose()
