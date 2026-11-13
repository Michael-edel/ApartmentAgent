from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import checker, listing_service, main, search_agent
from app.availability import restore_verified_check_history
from app.importer import ListingArchived, ListingImportError, ListingUnavailable
from app.models import BathroomCheckControl, BathroomCheckJob, BathroomCheckTask, Base, Listing, ListingCheck, ListingLike, SearchResult


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
async def test_saved_city_scope_and_browser_import(sessions):
    async with sessions.begin() as session:
        astana, ekibastuz, old = (listing(number) for number in (31, 32, 33))
        ekibastuz.city = "Экибастуз"
        old.city = "Шымкент"
        session.add_all([astana, ekibastuz, old])
        session.add_all([ListingLike(url=advert(number)) for number in (31, 32, 33)])
        session.add(SearchResult(url=advert(33), city="astana", search_engine="fixture",
                                 query="", title="2-комнатная квартира, 60 м², 25 млн тг"))

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for city, expected_id in (("astana", 31), ("ekibastuz", 32)):
            saved = await client.get("/api/v1/listings", params={"city": city})
            liked = await client.get("/api/v1/comparison/liked", params={"city": city})
            assert [row["id"] for row in saved.json()] == [expected_id]
            assert [row["listing_id"] for row in liked.json()] == [expected_id]
        assert (await client.get("/api/v1/listings/33")).json()["city"] == "Шымкент"
        assert (await client.get("/api/v1/listings", params={"city": "shymkent"})).status_code == 422

        import_url = "/api/v1/listings/browser-import"
        payload = {"source_url": advert(34), "title": "Квартира 34", "price_kzt": 25_000_000,
                   "area_m2": 60, "rooms": 2}
        assert (await client.post(import_url, json=payload)).status_code == 422
        assert (await client.post(import_url, json={**payload, "city": "Конаев", "city_observed": True})).status_code == 422
        assert (await client.post(import_url, json={**payload, "city": "Экибастуз", "city_observed": True})).status_code == 422
        assert (await client.post(import_url, json={**payload, "city": "Астана", "city_observed": True})).status_code == 201

        manual_url = advert(35)
        async with sessions.begin() as session:
            job = BathroomCheckJob(status="running", criteria={"manual_import": True, "source_url": manual_url})
            session.add(job)
            await session.flush()
            session.add(BathroomCheckControl(id=1, current_job_id=job.id))
            session.add(BathroomCheckTask(job_id=job.id, url=manual_url, status="leased",
                                          lease_expires_at=datetime.now(UTC) + timedelta(minutes=1)))
        manual = await client.post(import_url, json={**payload, "source_url": manual_url,
                                                    "city": "Экибастуз", "city_observed": True})
        assert manual.status_code == 201
        assert manual.json()["city"] == "Экибастуз"


@pytest.mark.asyncio
async def test_manual_olx_update_and_cross_site_duplicate(sessions, monkeypatch):
    olx_url = "https://www.olx.kz/d/obyavlenie/2h-komnatnaya-kvvrtira-67-kv-m-IDr7f0E.html"
    krisha_url = "https://krisha.kz/a/show/1015478864"
    async with sessions.begin() as session:
        session.add(Listing(source_url=krisha_url, title="2-комнатная квартира", city="Астана",
                            address="Ауэзова 22", rooms=2, area_m2=67.1, floor=5, floors_total=9,
                            price_kzt=35_500_000))

    async def unexpected_fetch(*args, **kwargs):
        pytest.fail("OLX manual import and checks must not fetch an OLX page")

    monkeypatch.setattr(checker, "import_krisha_listing", unexpected_fetch)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        path = "/api/v1/listings/olx-import"
        data = {"source_url": olx_url + "?reason=observed", "city": "Астана",
                "title": "2-комнатная квартира на Ауэзова 22", "address": "ул. Ауэзова 22",
                "rooms": 2, "area_m2": 67, "floor": 5, "floors_total": 9, "price_kzt": 36_000_000}
        invalid = await client.post(path, json={**data, "source_url": olx_url.replace("olx.kz", "olx.kz.example")})
        assert invalid.status_code == 422
        created = await client.post(path, json=data)
        assert created.status_code == 201
        card = created.json()
        assert card["source_url"] == olx_url
        assert card["price_kzt"] == 36_000_000
        assert card["krisha_city_price_per_m2"] is None
        card_path = f'/api/v1/listings/{card["id"]}'
        duplicates = (await client.get(card_path + "/duplicates")).json()
        assert {offer["source_url"]: offer["price_kzt"] for offer in duplicates["items"]} == {
            olx_url: 36_000_000, krisha_url: 35_500_000,
        }
        assert duplicates["items"][1]["evidence"]["kind"] == "same_house_floor_area"
        assert (await client.post(card_path + "/check")).json()["status"] == "manual"

        # Simulate a user entering a new price from the same ad using an updated slug.
        variant = "https://olx.kz/kk/d/obyavlenie/novoe-nazvanie-IDr7f0E.html"
        saved = (await client.post(path, json={**data, "source_url": variant, "price_kzt": 35_900_000})).json()
        assert saved["id"] == card["id"] and saved["price_kzt"] == 35_900_000
        assert len((await client.get("/api/v1/listings?city=astana")).json()) == 2
        prices = (await client.get(card_path + "/price-history")).json()
        assert [item["price_kzt"] for item in prices] == [35_900_000, 36_000_000]


@pytest.mark.asyncio
async def test_market_filter_applies_to_found_saved_map_and_favorites(sessions, monkeypatch):
    monkeypatch.setattr(main.settings, "search_city", "astana")
    async with sessions.begin() as session:
        for number, market in ((41, "new"), (42, "secondary"), (43, None)):
            card = listing(number)
            card.market_type = market
            session.add(card)
            await session.flush()
            session.add(ListingLike(url=advert(number)))
            session.add(SearchResult(url=advert(number), city="astana", market_type=None,
                                     imported_listing_id=number, search_engine="fixture", query="",
                                     title="2-комнатная квартира, 60 м², 25 млн тг"))

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for market, expected in (("new", {41}), ("secondary", {42}), ("all", {41, 42, 43})):
            monkeypatch.setattr(main.settings, "market_type", market)
            params = {"city": "astana", "market_type": market}
            saved = (await client.get("/api/v1/listings", params=params)).json()
            liked = (await client.get("/api/v1/comparison/liked", params=params)).json()
            found = (await client.get("/api/v1/search/results")).json()
            pins = (await client.get("/api/v1/search/map")).json()
            assert {row["id"] for row in saved} == expected
            assert {row["listing_id"] for row in liked} == expected
            assert {row["imported_listing_id"] for row in found} == expected
            assert {row["listing_id"] for row in pins["items"]} == expected
            assert pins["market_type"] == market


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
