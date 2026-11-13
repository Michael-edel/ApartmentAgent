from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main, search_agent
from app.ai_service import current_analysis
from app.config import get_settings
from app.krisha_search import build_krisha_search_url
from app.main import SearchRunRequest
from app.models import Base
from app.schema_migrations import ensure_legacy_columns
from app.schemas import ListingCreate
from app.scoring import assess_listing
from app.search_analysis import is_target_search_result


def test_existing_preference_table_migrates_without_losing_rooms():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        connection.execute(text("ALTER TABLE search_preferences DROP COLUMN min_price_kzt"))
        connection.execute(text("ALTER TABLE search_preferences DROP COLUMN max_price_kzt"))
        connection.execute(text("INSERT INTO search_preferences (id, rooms) VALUES (1, 4)"))
        ensure_legacy_columns(connection)
        assert connection.execute(text("SELECT rooms, min_price_kzt, max_price_kzt FROM search_preferences")).one() == (4, None, None)
        ensure_legacy_columns(connection)


def test_price_affects_query_filter_assessment_and_stale_analysis():
    settings = get_settings()
    original = settings.max_price_kzt
    try:
        settings.max_price_kzt = 40_000_000
        params = parse_qs(urlparse(build_krisha_search_url(rooms=3)).query)
        assert params["das[price][to]"] == ["40000000"]
        title = "3-комнатная квартира, 60 м², 35 млн тг"
        criteria = dict(min_area=55, max_area=70, target_rooms=3)
        assert is_target_search_result(title, None, max_price=40_000_000, **criteria)
        assert not is_target_search_result(title, None, max_price=30_000_000, **criteria)
        listing = ListingCreate(source_url="https://example.com/a", title=title,
                                price_kzt=35_000_000, area_m2=60, rooms=3)
        old = settings.target_rooms
        settings.target_rooms = 3
        assessment = assess_listing(listing, settings)
        remote = {"provider": "remote-ai", "target_rooms": 3, "max_price_kzt": 30_000_000,
                  "score": assessment.score, "recommendation": assessment.verdict}
        assert current_analysis(listing, assessment, remote)["provider"] == "local-rules-ai"
        settings.max_price_kzt = 30_000_000
        assert assess_listing(listing, settings).score < assessment.score
    finally:
        settings.max_price_kzt = original
        settings.target_rooms = old


@pytest.mark.parametrize("value", [0, -1, 10_000_000_001, 1.5, "30000000"])
def test_price_validation_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        SearchRunRequest(rooms=3, max_price_kzt=value)


@pytest.mark.asyncio
async def test_minimum_price_persists_and_can_be_cleared(monkeypatch):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(search_agent, "SessionLocal", sessions)
    settings = get_settings()
    for key, value in {"search_enabled": False, "target_rooms": 2,
                       "min_price_kzt": None, "max_price_kzt": 30_000_000}.items():
        monkeypatch.setattr(settings, key, value)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        endpoint = "/api/v1/search/run"
        result = await client.post(endpoint, json={"rooms": 4, "min_price_kzt": 25_000_000, "max_price_kzt": 50_000_000})
        assert result.status_code == 200
        assert (await client.post(endpoint, json={"rooms": 2, "max_price_kzt": 20_000_000})).status_code == 422
        settings.min_price_kzt, settings.max_price_kzt = None, 30_000_000
        await search_agent.load_search_preference()
        status = (await client.get("/api/v1/search/status")).json()
        assert (status["rooms"], status["min_price_kzt"], status["max_price_kzt"]) == (4, 25_000_000, 50_000_000)
        assert (await client.post(endpoint, json={"rooms": 4})).status_code == 200
        assert settings.min_price_kzt == 25_000_000
        assert (await client.post(endpoint, json={"rooms": 4, "min_price_kzt": None})).status_code == 200
        settings.min_price_kzt = 25_000_000
        await search_agent.load_search_preference()
        assert settings.min_price_kzt is None and settings.max_price_kzt == 50_000_000
    await engine.dispose()
