from types import SimpleNamespace

import pytest

from app.search_analysis import parse_bathrooms_count
from app import search_agent
from app.schemas import ListingCreate


def test_explicit_count_and_krisha_separate_bathroom_option():
    assert parse_bathrooms_count('4-комнатная, 90 м², 2 с/у') == 2
    assert parse_bathrooms_count('Санузел: 3') == 3
    assert parse_bathrooms_count('санузел раздельный') == 1
    assert parse_bathrooms_count('Санузел: РАЗДЕЛЬНЫЙ') == 1
    assert parse_bathrooms_count('Санузел: совмещённый') == 1
    assert parse_bathrooms_count('Санузел совмещенный') == 1
    assert parse_bathrooms_count('раздельный') is None
    assert parse_bathrooms_count('раздельный вход в квартиру') is None
    assert parse_bathrooms_count('Санузел раздельный, всего 2 с/у') == 2


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
    assert _bathrooms_fields('<li class="a-list__item">Санузел раздельный</li>', []) == (1, False)
    assert _bathrooms_fields('', [{'bathroomsCount': 'раздельный'}]) == (1, False)
    for layout in ('совмещённый', 'совмещенный'):
        assert _bathrooms_fields('', [{'bathroomsCount': layout}]) == (1, False)
        assert _bathrooms_fields(f'<li class="a-list__item">Санузел {layout}</li>', []) == (1, False)
    assert _bathrooms_fields('<li class="a-list__item">Санузел раздельный</li>', [{'bathroomsCount': 2}]) == (1, False)
    assert _bathrooms_fields('<li class="a-list__item">Санузел 2 с/у и более</li>', [{'bathroomsCount': 'раздельный'}]) == (2, True)


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


@pytest.mark.parametrize('value, expected', [
    ('раздельный', (1, False)),
    ('совмещенный', (1, False)),
    ('совмещённый', (1, False)),
    ('2 с/у и более', (2, True)),
])
@pytest.mark.parametrize('template', [
    '<div class="offer__info-item"><div class="offer__info-title"><span>Санузел</span></div>'
    '<div class="offer__info-value"><strong>{value}</strong></div></div>',
    '<div data-name="flat.toilet"><div>{value}</div></div>',
    '<dl><dt><span>Санузел</span></dt><dd><div>{value}</div></dd></dl>',
    '<li class="a-list__item"><div>Санузел</div><div><span>{value}</span></div></li>',
])
def test_bathroom_nested_labelled_fields_override_json(template, value, expected):
    from app.importer import _bathrooms_fields

    stale_json = [{'bathroomsCount': 5}]
    assert _bathrooms_fields(template.format(value=value), stale_json) == expected


@pytest.mark.parametrize('html', [
    '<p>Реклама: Санузел 2 с/у и более</p>',
    '<div class="offer__info-item"><div>Описание</div><div>Санузел 2 с/у и более</div></div>',
    '<dl><dt>Комнат</dt><dd>2</dd><dt>Описание</dt><dd>2 с/у</dd></dl>',
    '<dl><dt>Санузел</dt><div>Реклама</div><dd>2 с/у и более</dd></dl>',
    '<div hidden><div class="offer__info-item">Санузел 2 с/у и более</div></div>',
    '<div class="offer__info-item" style="display: none">Санузел 2 с/у и более</div>',
    '<dl><dt>Санузел</dt><dd aria-hidden="true">2 с/у и более</dd></dl>',
    '<div data-name="flat.toilet">неизвестно, возможно 2 с/у</div>',
])
def test_bathroom_field_parser_ignores_unlabelled_hidden_and_ambiguous_text(html):
    from app.importer import _bathrooms_fields

    assert _bathrooms_fields(html, []) == (None, False)


def test_hidden_bathroom_field_does_not_override_visible_and_unknown_stays_unknown():
    from app.importer import _bathrooms_fields

    html = ('<div hidden><div data-name="flat.toilet">2 с/у и более</div></div>'
            '<div class="offer__info-item"><div>Санузел</div><div>раздельный</div></div>')
    assert _bathrooms_fields(html, [{'bathroomsCount': 2}]) == (1, False)
    assert _bathrooms_fields('<dl><dt>Санузел</dt><dd>не указано</dd></dl>', []) == (None, False)
