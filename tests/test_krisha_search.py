from app.krisha_search import (
    build_krisha_search_url,
    normalize_krisha_listing_url,
    parse_krisha_search_page,
)
from app.search_providers import is_allowed_listing_url
from urllib.parse import parse_qs, urlparse
import pytest
from app.krisha_search import settings


@pytest.mark.parametrize(('minimum', 'expected'), [
    (2, ['2', '3', '4', '5.100']),
    (3, ['3', '4', '5.100']),
    (4, ['4', '5.100']),
])
def test_rooms_minimum_and_price_interval_in_official_query(monkeypatch, minimum, expected):
    monkeypatch.setattr(settings, 'min_price_kzt', 25_000_000)
    monkeypatch.setattr(settings, 'max_price_kzt', 50_000_000)
    params = parse_qs(urlparse(build_krisha_search_url(rooms=minimum, owner_only=True)).query)
    assert params['das[live.rooms][]'] == expected
    assert params['das[price][from]'] == ['25000000']
    assert params['das[price][to]'] == ['50000000']
    assert params['das[who]'] == ['1']


def test_no_minimum_price_omits_lower_bound(monkeypatch):
    monkeypatch.setattr(settings, 'min_price_kzt', None)
    assert 'das[price][from]' not in parse_qs(urlparse(build_krisha_search_url()).query)


def test_build_search_url_contains_filters() -> None:
    url = build_krisha_search_url(page=2)

    assert url.startswith("https://krisha.kz/prodazha/kvartiry/astana/")
    assert parse_qs(urlparse(url).query)["das[live.rooms][]"] == ["2", "3", "4", "5.100"]
    assert "das%5Bprice%5D%5Bto%5D=30000000" in url
    assert "page=2" in url


def test_normalize_listing_url_removes_query_and_fragment() -> None:
    value = normalize_krisha_listing_url(
        "https://www.krisha.kz/a/show/123456?utm_source=test#map"
    )

    assert value == "https://krisha.kz/a/show/123456"


def test_parse_search_page_collects_and_deduplicates_links() -> None:
    html = """
    <html><body>
      <a class="a-card__title" href="/a/show/111">2-комнатная квартира, 60 м²</a>
      <a href="https://krisha.kz/a/show/111?from=search">Повтор</a>
      <a href="/a/show/222">Другое объявление</a>
    </body></html>
    """

    items = parse_krisha_search_page(html)

    assert [item.url for item in items] == [
        "https://krisha.kz/a/show/111",
        "https://krisha.kz/a/show/222",
    ]
    assert items[0].title == "2-комнатная квартира, 60 м²"


def test_search_cards_pair_title_with_their_own_price() -> None:
    html = '''
    <div class="a-card__main-info"><div class="a-card__header-left">
      <a href="/a/show/111" class="a-card__title">3-комнатная квартира · 80 м²</a>
    </div><div class="a-card__price">34&nbsp;000&nbsp;000&nbsp;<span>₸</span></div></div>
    <div class="a-card__main-info"><div><a class="a-card__title" href="/a/show/222">
      3-комнатная квартира · 100 м²</a></div><div class="a-card__price">45&nbsp;000&nbsp;000 <span>₸</span></div></div>
    <div class="a-card__title-info"><div class="a-card__price">от 39&nbsp;000&nbsp;000 <span>₸</span></div>
      <a href="/a/show/333" class="a-card__title link">3-комнатная квартира · 77 м²</a></div>
    '''
    items = parse_krisha_search_page(html)
    assert [(item.url, item.snippet) for item in items] == [
        ("https://krisha.kz/a/show/111", "34 000 000 ₸"),
        ("https://krisha.kz/a/show/222", "45 000 000 ₸"),
        ("https://krisha.kz/a/show/333", "от 39 000 000 ₸"),
    ]


def test_search_provider_rejects_lookalike_host() -> None:
    assert is_allowed_listing_url("https://notkrisha.kz/a/show/123") is False
