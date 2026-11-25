import asyncio
from datetime import UTC, datetime
from secrets import token_hex
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import commercial, commercial_browser
from app.main import app
from app.models import Base, CommercialListing
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


async def test_previous_commercial_schema_retains_its_baseline_and_saved_count(monitor):
    client, sessions, _ = monitor
    started = datetime(2026, 11, 24, 8, tzinfo=UTC)
    seen = datetime(2026, 11, 24, 8, 15, tzinfo=UTC)
    async with sessions.begin() as session:
        await session.execute(text("ALTER TABLE commercial_scans DROP COLUMN last_checked_at"))
        await session.execute(
            text("INSERT INTO commercial_scans (deal_type, initialized, started_at) "
                 "VALUES ('olx-rent', TRUE, :started)"),
            {"started": started.isoformat()},
        )
        session.add(CommercialListing(
            url="olx:legacy", deal_type="rent", area=105, title="Legacy OLX rental", seen_at=seen,
        ))
        database = await session.connection()
        await database.run_sync(ensure_legacy_columns)

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

    await commercial.run_once(settings, krisha_retry_at=asyncio.get_running_loop().time() + 300)

    assert fetch.await_count == 2
    assert (await client.get(STATUS_URL)).json() == status


async def test_disabling_during_an_olx_fetch_discards_its_result(monitor, monkeypatch):
    client, _, settings = monitor
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
