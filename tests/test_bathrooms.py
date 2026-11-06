from types import SimpleNamespace

from app.search_analysis import parse_bathrooms_count
from app import search_agent
from app.schemas import ListingCreate


def test_explicit_count_does_not_infer_from_type():
    assert parse_bathrooms_count('4-комнатная, 90 м², 2 с/у') == 2
    assert parse_bathrooms_count('Санузел: 3') == 3
    assert parse_bathrooms_count('санузел раздельный') is None


def test_filter_requires_confirmed_bathroom_count(monkeypatch):
    monkeypatch.setattr(search_agent.settings, 'target_rooms', 3)
    monkeypatch.setattr(search_agent.settings, 'min_area_m2', 75)
    monkeypatch.setattr(search_agent.settings, 'max_area_m2', 110)
    monkeypatch.setattr(search_agent.settings, 'max_price_kzt', 45_000_000)
    monkeypatch.setattr(search_agent.settings, 'min_bathrooms', 2)
    row = SimpleNamespace(title='3-комнатная квартира, 80 м², 40 млн тг', snippet=None, bathrooms_count=None)
    assert search_agent._row_matches_basic(row)
    assert not search_agent._row_matches_target(row)
    row.bathrooms_count = 1
    assert not search_agent._row_matches_target(row)
    row.bathrooms_count = 2
    assert search_agent._row_matches_target(row)
    monkeypatch.setattr(search_agent.settings, 'min_bathrooms', 0)
    row.bathrooms_count = None
    assert search_agent._row_matches_target(row)


def test_listing_accepts_unknown_and_explicit_bathrooms():
    args = dict(source_url='https://krisha.kz/a/show/123', title='Квартира', price_kzt=30_000_000, area_m2=80, rooms=3)
    assert ListingCreate(**args).bathrooms_count is None
    assert ListingCreate(**args, bathrooms_count=2).bathrooms_count == 2


def test_lower_bound_fields_and_unknown():
    from app.importer import _bathrooms_fields
    from app.search_analysis import bathrooms_is_lower_bound
    assert bathrooms_is_lower_bound('Санузел: 2 с/у и более')
    assert not bathrooms_is_lower_bound('Санузел: 2')
    assert _bathrooms_fields('', [{'numberOfBathroomsTotal': 3}]) == (3, False)
    assert _bathrooms_fields('', [{'bathroomsCount': '2 с/у и более'}]) == (2, True)
    assert _bathrooms_fields('<li class="a-list__item">Санузел 2 с/у и более</li>', []) == (2, True)
    assert _bathrooms_fields('<li class="a-list__item">Санузел раздельный</li>', []) == (None, False)


def test_scoring_respects_bathroom_requirement():
    from app.config import Settings
    from app.scoring import assess_listing
    args = dict(source_url='https://krisha.kz/a/show/123', title='Квартира', price_kzt=29_000_000, area_m2=60, rooms=2)
    settings = Settings(min_bathrooms=2)
    unknown = assess_listing(ListingCreate(**args), settings)
    assert unknown.verdict != 'ПОКУПАТЬ'
    assert any('не подтверждено' in reason for reason in unknown.reasons)
    one = assess_listing(ListingCreate(**args, bathrooms_count=1), settings)
    assert one.verdict == 'НЕ РЕКОМЕНДУЮ'
    two = assess_listing(ListingCreate(**args, bathrooms_count=2), settings)
    assert two.score > unknown.score
