import pytest
from pydantic import ValidationError

from app.ai_service import build_local_analysis, current_analysis
from app.config import get_settings
from app.krisha_search import build_krisha_search_url
from app.main import SearchRunRequest
from app.schemas import ListingCreate
from app.scoring import assess_listing
from app.search_analysis import is_target_search_result


def test_area_request_validation():
    assert SearchRunRequest(rooms=3, min_area_m2=75.5, max_area_m2=100).max_area_m2 == 100
    for fields in ({"min_area_m2": 100, "max_area_m2": 75},
                   {"min_area_m2": 55}, {"min_area_m2": 0, "max_area_m2": 90},
                   {"min_area_m2": 1, "max_area_m2": float("inf")}):
        with pytest.raises(ValidationError):
            SearchRunRequest(rooms=3, **fields)
    assert SearchRunRequest(rooms=2).min_area_m2 is None


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
