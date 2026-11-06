from types import SimpleNamespace

import pytest

from app.listing_service import _find_listing, _values
from app.schemas import ListingCreate


def test_browser_url_is_normalized_before_saving():
    listing = ListingCreate(source_url="https://www.krisha.kz/a/show/123?from=search",
                            title="Тестовая квартира", price_kzt=25_000_000,
                            area_m2=65, rooms=3)
    assert _values(listing)["source_url"] == "https://krisha.kz/a/show/123"


@pytest.mark.asyncio
async def test_existing_noncanonical_krisha_url_is_reused():
    legacy = SimpleNamespace(source_url="https://www.krisha.kz/a/show/123?from=search")
    unrelated = SimpleNamespace(source_url="https://krisha.kz/a/show/1234")

    class Scalars:
        def all(self):
            return [unrelated, legacy]

    class Session:
        async def scalar(self, _query):
            return None

        async def scalars(self, _query):
            return Scalars()

    assert await _find_listing(Session(), "https://krisha.kz/a/show/123") is legacy
