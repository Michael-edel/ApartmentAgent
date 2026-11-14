from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import market_index as index
from app.models import Base, Listing, PriceSnapshot, SearchResult


@pytest.fixture
async def sessions(monkeypatch):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(index, "SessionLocal", factory)
    yield factory
    await engine.dispose()


def apartment(id, when, price=30_000_000, **overrides):
    values = dict(id=id, title="2-комнатная квартира", source_url=f"https://krisha.kz/a/show/{id}",
                  city="Астана", district="р-н Байконур", residential_complex="ЖК Пример",
                  rooms=2, area_m2=60, floor=5, price_kzt=price, building_type="кирпичный",
                  address=f"улица Примерная {id}", created_at=when, last_imported_at=when)
    return Listing(**(values | overrides))


def api():
    app = FastAPI()
    app.include_router(index.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://market.test")


@pytest.mark.asyncio
async def test_market_snapshot_only_uses_active_unique_local_sample_and_is_idempotent(sessions):
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    created = now - timedelta(days=3)
    async with sessions.begin() as session:
        session.add_all([apartment(id, created, price, seller_type="Хозяин недвижимости")
                         for id, price in enumerate(range(24_000_000, 54_000_000, 6_000_000), 1)])
        session.add_all([
            apartment(10, created, 90_000_000, address="улица Примерная 1"),
            apartment(11, created, sold_at=now),
            apartment(12, created, source_archived_at=now),
            apartment(13, created, source_unavailable_at=now),
            apartment(14, created, city="Экибастуз"),
            apartment(15, created, district="Нура", area_m2=55, building_type="монолитный", rooms=3),
            apartment(16, created, address="улица Примерная 11"),  # active duplicate of a sold apartment
        ])
    assert await index.record_market_snapshot(now=yesterday) > 0
    async with sessions.begin() as session:
        row = await session.get(Listing, 1)
        row.price_kzt = 60_000_000
    assert await index.record_market_snapshot(now=yesterday) == 0
    assert await index.record_market_snapshot(now=now) > 0
    async with api() as client:
        response = await client.get("/api/v1/market-index", params={"scope": "district", "location": "Байконур",
                                                                        "building_type": "brick", "rooms": 2, "area_band": "60_79"})
        assert response.status_code == 200
        data = response.json()
        assert data["series"] == [
            {"day": index._day(yesterday).isoformat(), "sample_size": 5, "median": 600_000, "p25": 500_000, "p75": 700_000},
            {"day": index._day(now).isoformat(), "sample_size": 5, "median": 700_000, "p25": 600_000, "p75": 800_000},
        ]
        city = (await client.get("/api/v1/market-index")).json()
        assert all(point["sample_size"] == 6 for point in city["series"])
        complex_series = (await client.get("/api/v1/market-index", params={"scope": "complex", "location": "ЖК Пример"})).json()
        assert complex_series["series"] == city["series"]
        small = (await client.get("/api/v1/market-index", params={"scope": "district", "location": "Нура"})).json()
        assert small["series"][0]["sample_size"] == 1 and small["series"][0]["median"] is None
        history = (await client.get("/api/v1/listings/1/market-history")).json()
        assert history["chart_window"] == {"start_day": index._start_day(now, 30).isoformat(),
                                           "end_day": index._day(now).isoformat(), "utc_offset_minutes": 300}
        assert history["district_comparison"] == {"window_days": 30, "median_price_per_m2": 650_000,
            "listing_price_per_m2": 1_000_000, "sample_days": 2, "latest_day": index._day(now).isoformat(),
            "difference_percent": 53.8}


@pytest.mark.asyncio
async def test_liquidity_uses_observations_and_distinct_end_reasons(sessions):
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add(apartment(1, now - timedelta(days=45), 45_000_000, last_imported_at=now - timedelta(days=1)))
        session.add(SearchResult(url="https://www.krisha.kz/a/show/1?q=legacy", city="astana",
                                 search_engine="fixture", query="", title="Квартира",
                                 first_seen=now - timedelta(days=50), last_seen=now - timedelta(days=1)))
        for offset, price in ((45, 50_000_000), (30, 48_000_000), (20, 49_000_000), (5, 45_000_000)):
            session.add(PriceSnapshot(listing_id=1, price_kzt=price, price_per_m2=round(price / 60),
                                      observed_at=now - timedelta(days=offset)))
        for id, end in ((2, "sold_at"), (3, "source_archived_at"), (4, "source_unavailable_at"),
                        (5, "source_archived_at"), (6, "source_unavailable_at")):
            session.add(apartment(id, now - timedelta(days=15), **{end: now - timedelta(days=5)}))
        session.add_all([
            apartment(7, now - timedelta(days=12), source_archived_at=now - timedelta(days=5)),
            apartment(8, now - timedelta(days=2), address="улица Примерная 7"),  # same property still active
            apartment(9, now - timedelta(days=55), sold_at=now - timedelta(days=40)),
            apartment(10, now - timedelta(days=50)),
            apartment(11, now - timedelta(days=10), district="Нура", sold_at=now - timedelta(days=1)),
        ])
    async with api() as client:
        response = await client.get("/api/v1/listings/1/market-history")
        assert response.status_code == 200
        data = response.json()
        liquidity = data["liquidity"]
        assert liquidity["observed_days"] == 49
        assert liquidity["price_change_count"] == 3 and liquidity["price_reduction_count"] == 2
        assert liquidity["total_reduction_kzt"] == 6_000_000 and liquidity["total_reduction_percent"] == 12
        recent = liquidity["cohorts"]["30"]
        assert (recent["sample_size"], recent["ended_count"], recent["sold_count"], recent["removed_count"]) == (6, 5, 1, 4)
        assert recent["archived_count"] == recent["unavailable_count"] == 2
        assert (recent["ended_fraction"], recent["removed_fraction"], recent["sold_fraction"]) == (.8333, .6667, .1667)
        assert recent["mean_ended_observed_days"] == 10 and recent["ended_duration_sample_size"] == 5
        broad = liquidity["cohorts"]["90"]
        assert (broad["sample_size"], broad["ended_count"], broad["sold_count"]) == (8, 6, 2)
        assert liquidity["signal"]["code"] == "negotiable"
        assert data["city"]["series"] == []  # price history does not synthesize daily market points
