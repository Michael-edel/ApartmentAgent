from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import checker, listing_service, main, search_agent
from app.availability import restore_verified_check_history
from app.importer import ListingArchived, ListingImportError, ListingUnavailable
from app.models import Base, Listing, ListingCheck, SearchResult


def advert(number: int) -> str:
    return f"https://krisha.kz/a/show/{number}"


@pytest.fixture
async def sessions(monkeypatch):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for module in (main, search_agent, checker, listing_service):
        monkeypatch.setattr(module, "SessionLocal", factory)
    for key, value in dict(target_rooms=2, min_price_kzt=None, max_price_kzt=40_000_000,
                           min_area_m2=50, max_area_m2=None, min_bathrooms=0,
                           ai_enabled=False, telegram_enabled=False, twogis_enabled=False).items():
        monkeypatch.setattr(main.settings, key, value)
    yield factory
    await engine.dispose()


def listing(number: int) -> Listing:
    return Listing(id=number, source_url=advert(number), title=f"Квартира {number}", rooms=2,
                   area_m2=60, price_kzt=25_000_000, latitude=51.1283, longitude=71.4306)


@pytest.mark.asyncio
async def test_sold_and_source_status_hide_inactive_offers_without_losing_saved_cards(sessions, monkeypatch):
    async with sessions.begin() as session:
        session.add_all([listing(11), listing(12), listing(13)])
        for number in (11, 12, 13, 14):
            session.add(SearchResult(url=f"http://www.krisha.kz/a/show/{number}/?q=legacy",
                                     search_engine="fixture", query="", title="2-комнатная квартира, 60 м², 25 млн тг"))

    async def source_error(url):
        if url == advert(11):
            raise ListingArchived("Архивный баннер")
        if url == advert(12):
            raise ListingUnavailable("Источник вернул HTTP 410")
        raise ListingImportError("Источник ограничил автоматический доступ (HTTP 468)")

    monkeypatch.setattr(checker, "import_krisha_listing", source_error)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        sale_url = "/api/v1/listings/11/sold"
        sold = (await client.put(sale_url, json={"sold": True})).json()
        assert sold["availability"] == "sold" and sold["sold_at"]
        assert (await client.post("/api/v1/listings/11/check")).json()["status"] == "archived"
        both = (await client.get("/api/v1/listings/11")).json()
        assert both["availability"] == "sold" and both["source_archived_at"]
        restored = (await client.put(sale_url, json={"sold": False})).json()
        assert restored["availability"] == "archived" and restored["sold_at"] is None

        assert (await client.post("/api/v1/listings/12/check")).json()["status"] == "unavailable"
        unavailable = (await client.get("/api/v1/listings/12")).json()
        assert unavailable["source_unavailable_at"] and unavailable["source_archived_at"] is None
        assert (await client.post("/api/v1/listings/13/check")).json()["status"] == "blocked"
        assert (await client.get("/api/v1/listings/13")).json()["availability"] == "active"
        archive = await client.post("/api/v1/listings/availability",
                                    json={"source_url": advert(14), "availability_status": "archived"})
        assert archive.json() == {"id": None, "source_url": advert(14), "availability_status": "archived"}
        assert {row["id"] for row in (await client.get("/api/v1/listings")).json()} == {11, 12, 13}
        results = (await client.get("/api/v1/search/results")).json()
        assert len(results) == 1 and "/13/" in results[0]["url"]
        maps = (await client.get("/api/v1/search/map")).json()
        assert [row["listing_id"] for row in maps["items"]] == [13]
        assert maps["without_coordinates"] == 0
        assert (await client.get("/api/v1/search/status")).json()["total_results"] == 1

        assert (await client.put(sale_url, json={"sold": True})).json()["availability"] == "sold"
        response = await client.post("/api/v1/listings/browser-import", json={
            "source_url": advert(11), "title": "Квартира 11", "price_kzt": 25_000_000,
            "area_m2": 60, "rooms": 2, "details_extracted": True, "availability_status": "active",
        })
        assert response.status_code == 201
        assert response.json()["source_archived_at"] is None
        assert response.json()["availability"] == "sold"
        assert (await client.put(sale_url, json={"sold": False})).json()["availability"] == "active"
        assert (await client.get("/api/v1/search/status")).json()["total_results"] == 2


@pytest.mark.asyncio
async def test_legacy_availability_uses_only_latest_definitive_check(sessions):
    before = datetime.now(UTC) - timedelta(days=3)
    after = before + timedelta(days=1)
    async with sessions.begin() as session:
        session.add_all([listing(21), listing(22), listing(23), listing(24)])
        await session.flush()
        session.add_all([
            ListingCheck(listing_id=21, status="archived", checked_at=before),
            ListingCheck(listing_id=21, status="unchanged", checked_at=after),
            ListingCheck(listing_id=22, status="unavailable", checked_at=before),
            ListingCheck(listing_id=22, status="blocked", checked_at=after),
            ListingCheck(listing_id=23, status="error", message="Источник вернул HTTP 404", checked_at=after),
            ListingCheck(listing_id=24, status="archived", checked_at=after),
        ])
    async with sessions.begin() as session:
        await restore_verified_check_history(session)
    async with sessions() as session:
        assert (await session.get(Listing, 21)).source_archived_at is None
        assert (await session.get(Listing, 22)).source_unavailable_at is not None
        assert (await session.get(Listing, 23)).source_availability_observed_at is None
        assert (await session.get(Listing, 24)).source_archived_at is not None
