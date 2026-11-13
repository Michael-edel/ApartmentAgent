import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main, search_agent
from app.ai_service import build_local_analysis, current_analysis
from app.bathroom_queue import _candidates, _criteria
from app.config import get_settings
from app.krisha_search import build_krisha_search_url
from app.main import SearchRunRequest
from app.models import Base, SearchPreference, SearchResult
from app.schemas import ListingCreate
from app.scoring import assess_listing
from app.search_analysis import is_target_search_result


def test_area_request_validation():
    assert SearchRunRequest(rooms=3, min_area_m2=75.5, max_area_m2=100).max_area_m2 == 100
    for fields in ({"min_area_m2": 100, "max_area_m2": 75},
                   {"min_area_m2": None}, {"min_area_m2": 0, "max_area_m2": 90},
                   {"min_area_m2": 1, "max_area_m2": float("inf")}):
        with pytest.raises(ValidationError):
            SearchRunRequest(rooms=3, **fields)
    assert SearchRunRequest(rooms=2).min_area_m2 is None
    assert SearchRunRequest(rooms=3, min_area_m2=55).min_area_m2 == 55
    assert SearchRunRequest(rooms=3, min_area_m2=80, max_area_m2=None).max_area_m2 is None


def test_selected_area_query_filter_scoring_and_analysis():
    settings = get_settings()
    old_min, old_max = settings.min_area_m2, settings.max_area_m2
    try:
        settings.min_area_m2, settings.max_area_m2 = 75.5, 100.0
        url = build_krisha_search_url(rooms=2)
        assert 'das%5Blive.square%5D%5Bfrom%5D=75.5' in url
        assert 'das%5Blive.square%5D%5Bto%5D=100.0' in url
        params = dict(max_price=30_000_000, min_area=75.5, max_area=100, target_rooms=2)
        assert is_target_search_result('2-комнатная квартира, 80 м², 29 млн тг', None, **params)
        assert not is_target_search_result('2-комнатная квартира, 60 м², 29 млн тг', None, **params)
        listing = ListingCreate(source_url='https://example.com/a', title='2-комнатная квартира',
                                price_kzt=29000000, area_m2=80, rooms=2)
        assessment = assess_listing(listing, settings)
        assert assessment.score == 100
        assert any('Площадь 80' in s for s in build_local_analysis(listing, assessment)['strengths'])
        stale_remote = {'provider': 'remote-ai', 'target_rooms': settings.target_rooms,
                        'max_price_kzt': settings.max_price_kzt, 'min_area_m2': 55,
                        'max_area_m2': 70, 'score': assessment.score,
                        'recommendation': assessment.verdict}
        assert current_analysis(listing, assessment, stale_remote)['provider'] == 'local-rules-ai'
        listing.area_m2 = 60
        outside = assess_listing(listing, settings)
        assert any('75.5–100' in reason for reason in outside.reasons)
    finally:
        settings.min_area_m2, settings.max_area_m2 = old_min, old_max


@pytest.mark.asyncio
async def test_maximum_area_can_be_cleared_and_persists_across_reload(monkeypatch):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(search_agent, "SessionLocal", sessions)
    settings = get_settings()
    for key, value in {"search_enabled": False, "target_rooms": 2, "min_area_m2": 55,
                       "max_area_m2": 100, "max_price_kzt": 30_000_000, "min_bathrooms": 0}.items():
        monkeypatch.setattr(settings, key, value)

    async with sessions() as session:
        session.add(SearchPreference(id=1, rooms=2))
        for advert, area in ((123, 160), (124, 50)):
            session.add(SearchResult(url=f"https://krisha.kz/a/show/{advert}", city="astana", search_engine="fixture",
                                     query="fixture", title=f"2-комнатная квартира, {area} м², 29 млн тг"))
        await session.commit()
    await search_agent.load_search_preference()
    assert (settings.min_area_m2, settings.max_area_m2) == (55, 100)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        endpoint = "/api/v1/search/run"
        assert (await client.post(endpoint, json={"rooms": 2, "min_area_m2": 60})).status_code == 200
        assert settings.max_area_m2 == 100
        assert (await client.post(endpoint, json={"rooms": 2, "min_area_m2": 101})).status_code == 422
        assert (settings.min_area_m2, settings.max_area_m2) == (60, 100)
        assert (await client.post(endpoint, json={"rooms": 2, "max_area_m2": None})).status_code == 200
        settings.min_area_m2, settings.max_area_m2 = 55, 100
        await search_agent.load_search_preference()
        status = (await client.get("/api/v1/search/status")).json()
        assert (status["min_area_m2"], status["max_area_m2"]) == (60, None)
        assert (await client.post(endpoint, json={"rooms": 2})).status_code == 200
        assert settings.max_area_m2 is None
        found = (await client.get("/api/v1/search/results")).json()
        assert [row["analysis"]["area_m2"] for row in found] == [160]

    async with sessions() as session:
        assert [url for url, _ in await _candidates(session, _criteria())] == ["https://krisha.kz/a/show/123"]
    url = build_krisha_search_url()
    assert 'das%5Blive.square%5D%5Bfrom%5D=60.0' in url
    assert 'das%5Blive.square%5D%5Bto%5D' not in url
    listing = ListingCreate(source_url='https://example.com/a', title='2-комнатная квартира',
                            price_kzt=29000000, area_m2=160, rooms=2)
    assessment = assess_listing(listing, settings)
    assert assessment.score == 100
    assert any('Площадь 160' in strength for strength in build_local_analysis(listing, assessment)['strengths'])
    listing.area_m2 = 50
    assert 'Площадь меньше минимальной: 60 м²' in assess_listing(listing, settings).reasons
    await engine.dispose()
