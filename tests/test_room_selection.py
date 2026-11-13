from urllib.parse import parse_qs, urlparse

from app.config import Settings
from app.krisha_search import build_krisha_search_url
from app.schemas import ListingCreate
from app.scoring import assess_listing
from app.search_analysis import is_target_search_result


def test_room_selection_applies_to_query_filter_and_score():
    for rooms in (2, 3, 4):
        assert parse_qs(urlparse(build_krisha_search_url(rooms=rooms)).query)['das[live.rooms][]'] == [*(str(value) for value in range(rooms, 5)), '5.100']
        title = f'{rooms}-комнатная квартира, 60 м², 29 млн тг'
        criteria = dict(max_price=30000000, min_area=55, max_area=70)
        assert is_target_search_result(title, None, target_rooms=rooms, **criteria)
        assert not is_target_search_result(title, None, target_rooms=rooms + 1, **criteria)
        assert is_target_search_result(title, None, target_rooms=2, **criteria)
        listing = ListingCreate(source_url='https://example.com/a', title=title, price_kzt=29000000,
                                area_m2=60, rooms=rooms, is_full_two_room=rooms == 2)
        assert assess_listing(listing, Settings(target_rooms=rooms)).score == 100


def test_analysis_recalculates_after_room_preference_changes():
    from app.ai_service import build_local_analysis, current_analysis
    from app.config import get_settings

    settings = get_settings()
    original = settings.target_rooms
    try:
        listing = ListingCreate(source_url='https://example.com/a', title='3-комнатная квартира',
                                price_kzt=29000000, area_m2=60, rooms=3,
                                is_full_two_room=False)
        settings.target_rooms = 4
        old = build_local_analysis(listing, assess_listing(listing, settings))
        assert old['recommendation'] == 'НЕ РЕКОМЕНДУЮ'
        settings.target_rooms = 3
        assessment = assess_listing(listing, settings)
        refreshed = current_analysis(listing, assessment, old)
        assert refreshed['recommendation'] == assessment.verdict == 'ПОКУПАТЬ'
        assert refreshed['score'] == assessment.score
        remote_old = {'provider': 'remote-ai', 'target_rooms': 4, 'score': old['score']}
        assert current_analysis(listing, assessment, remote_old)['provider'] == 'local-rules-ai'
    finally:
        settings.target_rooms = original
