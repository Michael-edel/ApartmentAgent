from app.krisha_search import (
    build_krisha_search_url,
    normalize_krisha_listing_url,
    parse_krisha_search_page,
)
from app.search_providers import is_allowed_listing_url


def test_build_search_url_contains_filters() -> None:
    url = build_krisha_search_url(page=2)

    assert url.startswith("https://krisha.kz/prodazha/kvartiry/astana/")
    assert "das%5Blive.rooms%5D=2" in url
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


def test_search_provider_rejects_lookalike_host() -> None:
    assert is_allowed_listing_url("https://notkrisha.kz/a/show/123") is False
