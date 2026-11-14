from pathlib import Path

import httpx
import pytest

from app import olx
from app.krisha_search import parse_krisha_search_page

FIXTURES = Path(__file__).parent


def test_redacted_krisha_catalog_preserves_structured_card_data():
    source = (FIXTURES / "krisha-catalog-redacted.html").read_text(encoding="utf-8")
    rows = parse_krisha_search_page(source, expected_city="astana")
    assert len(rows) == 1
    row = rows[0]
    assert row.url == "https://krisha.kz/a/show/990001"
    assert row.title == "4-комнатная квартира · 90 м² · 3/5 этаж"
    assert row.snippet == "47 000 000 ₸"
    assert row.seller_type == "Хозяин недвижимости" and row.market_type == "secondary"


@pytest.mark.asyncio
async def test_redacted_olx_detail_preserves_structure_and_approximate_map(monkeypatch):
    source = (FIXTURES / "olx-offer-redacted.html").read_text(encoding="utf-8")
    url = "https://www.olx.kz/d/obyavlenie/example-IDfixture1.html"

    async def public_page(requested):
        assert requested == url
        return httpx.Response(200, text=source)

    monkeypatch.setattr(olx, "_get_olx", public_page)
    row = await olx.import_olx_listing(url, expected_city="astana")
    assert (row.rooms, row.area_m2, row.price_kzt) == (4, 90, 47_000_000)
    assert (row.city, row.district, row.market_type) == ("Астана", "Байконур", "secondary")
    assert row.bathrooms_count == 2 and row.bathrooms_count_is_lower_bound
    assert row.latitude is None and row.longitude is None
    assert (row.approximate_latitude, row.approximate_longitude) == (51.12, 71.42)
