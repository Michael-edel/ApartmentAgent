import asyncio
import json
from datetime import UTC, datetime
from secrets import token_hex
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import commercial, commercial_browser
from app.main import app
from app.models import Base, CommercialEvent, CommercialListing
from app.schema_migrations import ensure_legacy_columns

STATUS_URL = "/api/v1/commercial/status"
SETTINGS_URL = "/api/v1/commercial/settings"
BROWSER_URL = "/api/v1/commercial/browser"


@pytest.fixture
async def monitor(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    settings = SimpleNamespace(
        commercial_monitor_enabled=True,
        commercial_interval_minutes=5,
        telegram_enabled=True,
        telegram_bot_token=token_hex(16),
        telegram_chat_id="-123",
        telegram_business_thread_id=5,
    )
    monkeypatch.setattr(commercial, "SessionLocal", sessions)
    monkeypatch.setattr(commercial, "get_settings", lambda: settings)
    monkeypatch.setattr(commercial_browser, "get_settings", lambda: settings)
    monkeypatch.setattr(commercial_browser, "observe", AsyncMock())
    monkeypatch.setattr(commercial, "send_queued", AsyncMock(return_value=True))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://panel.test"
        ) as client:
            yield client, sessions, settings
    finally:
        await engine.dispose()


async def test_panel_override_controls_browser_scan(monitor):
    client, _, settings = monitor
    status = await client.get(STATUS_URL)
    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert status.json()["last_checked_at"] is None
    assert (await client.get(f"{BROWSER_URL}/plan")).json()["enabled"] is True

    disabled = await client.put(SETTINGS_URL, json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert (await client.get(STATUS_URL)).json()["enabled"] is False
    assert (await client.get(f"{BROWSER_URL}/plan")).json()["enabled"] is False
    denied = await client.post(
        f"{BROWSER_URL}/scan",
        json={"catalog": commercial.KRISHA_CATALOGS["property", "rent"], "cards": []},
    )
    assert denied.status_code == 409
    assert (await client.get(STATUS_URL)).json()["last_checked_at"] is None

    settings.commercial_monitor_enabled = False
    enabled = await client.put(SETTINGS_URL, json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert (await client.get(STATUS_URL)).json()["enabled"] is True
    assert (await client.get(f"{BROWSER_URL}/plan")).json()["enabled"] is True


async def test_panel_can_enable_an_env_disabled_monitor_without_telegram(monitor):
    client, _, settings = monitor
    settings.commercial_monitor_enabled = False
    settings.telegram_enabled = False
    assert (await client.get(STATUS_URL)).json()["enabled"] is False
    assert (await client.get(f"{BROWSER_URL}/plan")).json()["enabled"] is False

    response = await client.put(SETTINGS_URL, json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert (await client.get(f"{BROWSER_URL}/plan")).json()["enabled"] is True


async def test_panel_counts_saved_ads_and_records_even_an_empty_successful_scan(monitor):
    client, sessions, _ = monitor
    async with sessions() as session:
        session.add_all([
            CommercialListing(
                url="olx:old", deal_type="rent", area=105, title="Existing OLX rental"
            ),
            CommercialListing(
                url="olx:second", deal_type="sale", area=115, title="Existing OLX sale"
            ),
        ])
        await session.commit()

    status = (await client.get(STATUS_URL)).json()
    assert status["sources"]["olx"]["count"] == 2
    assert status["sources"]["krisha"]["count"] == 0
    assert status["sources"]["krisha"]["last_checked_at"] is None

    before_check = datetime.now(UTC)
    response = await client.post(
        f"{BROWSER_URL}/scan",
        json={"catalog": commercial.KRISHA_CATALOGS["business", "sale"], "cards": []},
    )
    assert response.status_code == 200
    assert response.json()["matching"] == 0
    status = (await client.get(STATUS_URL)).json()
    assert status["sources"]["olx"]["count"] == 2
    assert status["sources"]["krisha"]["count"] == 0
    timestamp = datetime.fromisoformat(status["sources"]["krisha"]["last_checked_at"])
    assert before_check <= timestamp <= datetime.now(UTC)
    assert datetime.fromisoformat(status["last_checked_at"]) == timestamp

    response = await client.post(
        f"{BROWSER_URL}/scan",
        json={
            "catalog": commercial.KRISHA_CATALOGS["property", "sale"],
            "cards": [
                {"url": "https://krisha.kz/a/show/1", "title": "Офис · 110 м²"},
                {"url": "https://krisha.kz/a/show/2", "title": "Офис · 90 м²"},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["matching"] == 1
    status = (await client.get(STATUS_URL)).json()
    assert status["sources"]["olx"]["count"] == 2
    assert status["sources"]["krisha"]["count"] == 1


async def test_rejected_browser_report_does_not_advance_last_check(monitor):
    client, _, _ = monitor
    catalog = commercial.KRISHA_CATALOGS["property", "rent"]
    response = await client.post(f"{BROWSER_URL}/scan", json={"catalog": catalog, "cards": []})
    assert response.status_code == 200
    status = (await client.get(STATUS_URL)).json()
    assert status["last_checked_at"] is not None
    invalid = await client.post(
        f"{BROWSER_URL}/scan",
        json={"catalog": catalog, "cards": [{"url": "https://example.test", "title": "Офис"}]},
    )
    assert invalid.status_code == 422
    assert (await client.get(STATUS_URL)).json() == status


async def test_browser_looks_up_missing_photos_for_changed_listings_before_backfill(monitor):
    client, sessions, _ = monitor
    catalog = commercial.KRISHA_CATALOGS["property", "rent"]
    photo = "https://krisha-photos.kcdn.online/webp/example/1-750x470.jpg"
    unchanged = {"url": "https://krisha.kz/a/show/1", "title": "Офис · 110 м²", "price": "110 000 ₸"}
    changing = {"url": "https://krisha.kz/a/show/2", "title": "Офис · 120 м²", "price": "120 000 ₸"}
    saved_photo = {"url": "https://krisha.kz/a/show/3", "title": "Офис · 130 м²", "photo_urls": [photo]}
    small = {"url": "https://krisha.kz/a/show/4", "title": "Офис · 90 м²"}
    scan_url = f"{BROWSER_URL}/scan"
    candidate_url = f"{BROWSER_URL}/photo-candidates"
    assert (await client.post(scan_url, json={"catalog": catalog, "cards": [unchanged, changing, saved_photo]})).status_code == 200

    changed = {**changing, "price": "115 000 ₸"}
    saved_without_photo = {**saved_photo, "photo_urls": []}
    candidates = await client.post(candidate_url, json={"catalog": catalog, "cards": [
        unchanged, changed, saved_without_photo, small,
    ]})
    assert candidates.json() == {"urls": [changing["url"], unchanged["url"]]}
    async with sessions() as session:
        listing = await session.get(CommercialListing, changing["url"])
        assert listing.price == changing["price"]
        assert listing.photo_url is None

    result = await client.post(scan_url, json={"catalog": catalog, "cards": [{**changed, "photo_urls": [photo]}]})
    assert result.status_code == 200
    async with sessions() as session:
        listing = await session.get(CommercialListing, changing["url"])
        event = await session.scalar(select(CommercialEvent).where(CommercialEvent.url == changing["url"]))
        assert listing.price == changed["price"]
        assert listing.photo_url == event.photo_url == photo
        assert listing.photo_urls == [photo]

    candidates = await client.post(candidate_url, json={"catalog": catalog, "cards": [
        unchanged, changed, saved_without_photo, small,
    ]})
    assert candidates.json() == {"urls": [unchanged["url"]]}
    denied = await client.post(candidate_url, headers={"origin": "https://krisha.kz"}, json={
        "catalog": catalog, "cards": [unchanged],
    })
    assert denied.status_code == 403


async def test_browser_photo_queue_progresses_and_keeps_gallery_on_thumbnail_update(monitor):
    client, sessions, _ = monitor
    catalog = commercial.KRISHA_CATALOGS["property", "rent"]
    cards = [{"url": f"https://krisha.kz/a/show/{number}", "title": "Офис · 120 м²"} for number in range(10, 17)]
    first, missing, timed_out, *waiting = [card["url"] for card in cards]
    cdn = "https://kzala2-photos-kr.kcdn.kz/webp/82/8287575c-025b-41e9-88da-51970eb69f79/"
    cover, second, thumbnail = [cdn + filename for filename in ("1-750x470.webp", "2-750x470.webp", "1-120x90.jpg")]
    scan_url = f"{BROWSER_URL}/scan"
    candidate_url = f"{BROWSER_URL}/photo-candidates"
    payload = {"catalog": catalog, "cards": cards}
    assert (await client.post(scan_url, json=payload)).status_code == 200
    assert (await client.post(candidate_url, json=payload)).json() == {"urls": [first, missing, timed_out]}

    found = {**cards[0], "photo_url": thumbnail, "photo_urls": [cover, thumbnail, second, cdn + "empty-photo.jpg",
        "https://kzala2-photos-kr.kcdn.kz.example.test/untrusted.jpg"]}
    batch = {"catalog": catalog, "cards": [found, *cards[1:]], "photo_lookups": [
        {"url": first, "status": "found", "rendered": True},
        {"url": missing, "status": "metadata_missing", "rendered": True,
         "diagnostics": {"gallery_roots": 1, "primary_images": 3, "trusted_links": 0}},
        {"url": timed_out, "status": "render_timeout"},
    ]}
    result = await client.post(scan_url, json=batch)
    assert result.status_code == 200
    assert result.json()["with_photos"] == result.json()["with_galleries"] == 1
    assert (await client.post(candidate_url, json=payload)).json() == {"urls": waiting[:3]}

    assert (await client.post(scan_url, json={"catalog": catalog, "cards": [{**cards[0], "photo_url": thumbnail}]})).status_code == 200
    async with sessions() as session:
        listing = await session.get(CommercialListing, first)
        assert listing.photo_urls == [cover, second]
        assert listing.photo_url == cover
        assert (await session.get(CommercialListing, missing)).photo_checked_at is not None
        assert (await session.get(CommercialListing, timed_out)).photo_checked_at is not None
        assert (await session.get(CommercialListing, waiting[0])).photo_checked_at is None


async def test_browser_reports_verification_and_saves_partial_catalog_without_marking_it_complete(monitor):
    client, _, _ = monitor
    catalog = commercial.KRISHA_CATALOGS["property", "rent"]
    report = await client.post(f"{BROWSER_URL}/report", json={"catalog": catalog, "status": "verification"})
    assert report.status_code == 200
    commercial_browser.observe.assert_awaited_once_with("krisha", catalog, "browser", "blocked",
                                                        error="verification")

    cards = [{"url": "https://krisha.kz/a/show/1", "title": "Офис · 110 м²"}]
    partial = await client.post(f"{BROWSER_URL}/scan",
                                json={"catalog": catalog, "cards": cards, "complete": False})
    assert partial.status_code == 200
    assert partial.json()["matching"] == 1
    status = (await client.get(STATUS_URL)).json()
    assert status["sources"]["krisha"] == {"count": 1, "last_checked_at": None}

    complete = await client.post(f"{BROWSER_URL}/scan", json={"catalog": catalog, "cards": cards})
    assert complete.status_code == 200
    status = (await client.get(STATUS_URL)).json()
    assert status["sources"]["krisha"]["count"] == 1
    assert status["sources"]["krisha"]["last_checked_at"] is not None


async def test_tracking_marks_existing_listings_and_notifies_once_at_first_discovery(monitor):
    client, sessions, _ = monitor
    catalog = commercial.KRISHA_CATALOGS["property", "rent"]
    known = {"url": "https://krisha.kz/a/show/101", "title": "Офис · 110 м²",
             "price": "220 000 ₸ за месяц", "published_at": "2020-01-01T05:00:00+05:00"}
    new = {"url": "https://krisha.kz/a/show/102", "title": "Офис · 120 м²",
           "price": "300 000 ₸ за месяц"}
    assert (await client.post(f"{BROWSER_URL}/scan", json={"catalog": catalog, "cards": [known]})).status_code == 200
    started_at = await commercial.start_commercial_tracking()
    assert await commercial.start_commercial_tracking() == started_at
    async with sessions() as session:
        existing = await session.get(CommercialListing, known["url"])
        assert commercial._utc(existing.detected_at) == started_at
        assert commercial._utc(existing.published_at).year == 2020
        assert existing.price == known["price"]
        assert (await session.scalars(select(CommercialEvent))).all() == []

    for _ in range(2):
        assert (await client.post(f"{BROWSER_URL}/scan",
                                  json={"catalog": catalog, "cards": [known, new]})).status_code == 200
    status = (await client.get(STATUS_URL)).json()
    assert datetime.fromisoformat(status["tracking_started_at"]) == started_at
    async with sessions() as session:
        discovered = await session.get(CommercialListing, new["url"])
        discovery_at = discovered.detected_at
        assert commercial._utc(discovery_at) >= started_at
        assert discovered.published_at is None

    changed = {**known, "price": "200 000 ₸ за месяц 1 818 ₸ за м²"}
    for _ in range(2):
        assert (await client.post(f"{BROWSER_URL}/scan",
                                  json={"catalog": catalog, "cards": [changed, new]})).status_code == 200
    async with sessions() as session:
        events = (await session.scalars(select(CommercialEvent).order_by(CommercialEvent.id))).all()
        assert [event.url for event in events] == [new["url"], known["url"]]
        assert "Впервые найдено" in events[0].message and "Обнаружено" in events[0].message
        assert "220 000 ₸ за месяц → 200 000 ₸ за месяц" in events[1].message
        assert (await session.get(CommercialListing, new["url"])).detected_at == discovery_at


async def test_previous_commercial_schema_retains_its_baseline_and_saved_count(monitor):
    client, sessions, _ = monitor
    started = datetime(2026, 11, 24, 8, tzinfo=UTC)
    seen = datetime(2026, 11, 24, 8, 15, tzinfo=UTC)
    async with sessions.begin() as session:
        await session.execute(text("ALTER TABLE commercial_scans DROP COLUMN last_checked_at"))
        await session.execute(text("ALTER TABLE commercial_listings DROP COLUMN photo_urls"))
        await session.execute(text("ALTER TABLE commercial_listings DROP COLUMN photo_checked_at"))
        await session.execute(text("ALTER TABLE commercial_events DROP COLUMN deal_type"))
        await session.execute(
            text("INSERT INTO commercial_scans (deal_type, initialized, started_at) "
                 "VALUES ('olx-rent', TRUE, :started)"),
            {"started": started.isoformat()},
        )
        await session.execute(text(
            "INSERT INTO commercial_listings (url, deal_type, area, title, seen_at, photo_url) "
            "VALUES ('olx:legacy', 'rent', 105, 'Legacy OLX rental', :seen, :photo)"
        ), {"seen": seen.isoformat(), "photo": "https://a.olxcdn.com/legacy.jpg"})
        await session.execute(text(
            "INSERT INTO commercial_events (url, message, status, sent_with_photo) "
            "VALUES ('https://krisha.kz/a/show/765', 'Legacy notification', 'sent', TRUE)"
        ))
        database = await session.connection()
        await database.run_sync(ensure_legacy_columns)
        legacy = await session.get(CommercialListing, "olx:legacy")
        assert legacy.photo_urls == []
        assert legacy.photo_checked_at is None
        assert legacy.photo_url == "https://a.olxcdn.com/legacy.jpg"
        legacy_event = await session.scalar(select(CommercialEvent))
        assert legacy_event.status == 'sent' and legacy_event.deal_type is None
        assert legacy_event.sent_with_photo is True

    status = (await client.get(STATUS_URL)).json()
    assert status["sources"]["olx"]["count"] == 1
    assert datetime.fromisoformat(status["sources"]["olx"]["last_checked_at"]) == seen
    assert status["sources"]["krisha"] == {"count": 0, "last_checked_at": None}


async def test_failed_olx_fetch_leaves_the_last_success_unchanged(monitor, monkeypatch):
    client, _, settings = monitor
    checked = datetime(2026, 11, 24, 8, 15, tzinfo=UTC)
    await commercial.save_scan("rent", [], source="olx", checked_at=checked)
    status = (await client.get(STATUS_URL)).json()
    assert datetime.fromisoformat(status["sources"]["olx"]["last_checked_at"]) == checked
    fetch = AsyncMock(side_effect=RuntimeError("OLX catalog unavailable"))
    monkeypatch.setattr(commercial, "fetch_olx_catalog", fetch)
    monkeypatch.setattr(commercial, "fetch_etagi_catalog", AsyncMock(side_effect=RuntimeError("not requested")))
    async def observed(source, url, method, fetcher, *args):
        return await fetcher(*args)
    monkeypatch.setattr(commercial, "observed_fetch", observed)

    await commercial.run_once(settings, krisha_retry_at=asyncio.get_running_loop().time() + 300)

    assert fetch.await_count == 2
    assert (await client.get(STATUS_URL)).json() == status


async def test_etagi_baseline_and_price_change_are_tracked_in_correct_topic(monitor):
    client, sessions, settings = monitor
    item = commercial.EtagiCommercialItem(
        'Помещение · 120 м²', 'https://ekibastuz.etagi.com/commerce/123/',
        '20 000 000 ₸', 120, photo_url='https://cdn.esoft.digital/content/offer.jpg',
    )
    first = datetime(2026, 11, 27, 6, 0, tzinfo=UTC)
    await commercial.save_scan('sale', [item], source='etagi', checked_at=first)
    async with sessions() as session:
        assert await session.scalar(select(CommercialEvent.id)) is None
    item.snippet = '19 000 000 ₸'
    await commercial.save_scan('sale', [item], source='etagi', checked_at=first)
    async with sessions() as session:
        event = await session.scalar(select(CommercialEvent))
        assert event.deal_type == 'sale'
        assert '20 000 000' in event.message and '19 000 000' in event.message
        assert 'Этажи' in commercial._commercial_message(event.message, '27.11.2026 11:00')
        assert (await commercial._commercial_event_listing(session, event)).url == item.url
    status = (await client.get(STATUS_URL)).json()
    assert status['sources']['etagi']['count'] == 1
    assert datetime.fromisoformat(status['sources']['etagi']['last_checked_at']) == first
    assert status['sources']['krisha']['count'] == 0


async def test_etagi_catalog_excludes_recommendations_and_other_deals(monitor, monkeypatch):
    state = {'filters': {'commerce': {'count': 1, 'limit': 30}}, 'lists': {'commerce': [
        {'object_id': 1, 'class': 'offices', 'status': 'active', 'action_sl': 'sale',
         'square': 120, 'price': '20000000', 'meta': {'city': 'Экибастуз', 'street': 'Абая'}},
        {'object_id': 2, 'class': 'offices', 'status': 'active', 'action_sl': 'sale',
         'square': 120, 'price': '30000000', 'meta': {'city': 'Экибастуз'}},
    ]}}
    monkeypatch.setattr(commercial, 'get_etagi_page',
                        AsyncMock(return_value='<script>var data=' + json.dumps(state) + '</script>'))
    found = await commercial.fetch_etagi_catalog('sale')
    assert [row.url for row in found] == ['https://ekibastuz.etagi.com/commerce/1/']
    assert found[0].snippet == '20 000 000 ₸'


async def test_disabling_during_an_olx_fetch_discards_its_result(monitor, monkeypatch):
    client, _, settings = monitor
    monkeypatch.setattr(commercial, "fetch_etagi_catalog", AsyncMock(side_effect=RuntimeError("not requested")))
    async def observed(source, url, method, fetcher, *args):
        return await fetcher(*args)
    monkeypatch.setattr(commercial, "observed_fetch", observed)
    ad = commercial.OlxCommercialItem(
        "Склад", "https://www.olx.kz/d/obyavlenie/sklad-IDabc.html", "100 000 ₸", 120, None,
    )

    async def fetch_after_disabling(_):
        response = await client.put(SETTINGS_URL, json={"enabled": False})
        assert response.status_code == 200
        return [ad]

    fetch = AsyncMock(side_effect=fetch_after_disabling)
    monkeypatch.setattr(commercial, "fetch_olx_catalog", fetch)
    await commercial.run_once(settings, krisha_retry_at=asyncio.get_running_loop().time() + 300)

    assert fetch.await_count == 1
    status = (await client.get(STATUS_URL)).json()
    assert status["enabled"] is False
    assert status["sources"]["olx"] == {"count": 0, "last_checked_at": None}


async def test_enabling_from_panel_wakes_existing_scheduler(monitor, monkeypatch):
    client, _, settings = monitor
    settings.commercial_monitor_enabled = False
    changed, stop = asyncio.Event(), asyncio.Event()
    disabled_pass, enabled_pass = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(app.state, "commercial_monitor_changed", changed, raising=False)

    async def observe_run(settings, *, krisha_retry_at):
        if await commercial.commercial_monitor_enabled(settings):
            enabled_pass.set()
        else:
            disabled_pass.set()
        return krisha_retry_at

    monkeypatch.setattr(commercial, "run_once", observe_run)
    scheduler = asyncio.create_task(commercial.periodic_commercial(stop, changed))
    try:
        await asyncio.wait_for(disabled_pass.wait(), timeout=2)
        assert (await client.put(SETTINGS_URL, json={"enabled": True})).status_code == 200
        await asyncio.wait_for(enabled_pass.wait(), timeout=2)
    finally:
        stop.set()
        await asyncio.wait_for(scheduler, timeout=2)
