from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text

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
        connection.execute(text("ALTER TABLE search_preferences DROP COLUMN max_price_kzt"))
        connection.execute(text("INSERT INTO search_preferences (id, rooms) VALUES (1, 4)"))
        ensure_legacy_columns(connection)
        assert connection.execute(text("SELECT rooms, max_price_kzt FROM search_preferences")).one() == (4, None)
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
