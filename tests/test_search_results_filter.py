from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.search_analysis import analyze_search_result, is_target_search_result


def test_search_parses_unspaced_and_large_prices_without_truncation():
    criteria = dict(max_price=45_000_000, min_area=75, max_area=110, target_rooms=3)
    for title, price in [("3-комнатная квартира, 80 м², 100 млн тг", 100_000_000),
                         ("3-комнатная квартира, 80 м², 50000000 ₸", 50_000_000)]:
        assert analyze_search_result(title, None, **criteria)["price_kzt"] == price
        assert not is_target_search_result(title, None, **criteria)
    assert is_target_search_result("3-комнатная квартира, 80 м², 40 млн тг", None, **criteria)


def test_lower_bound_price_is_not_treated_as_confirmed_price():
    criteria = dict(max_price=45_000_000, min_area=75, max_area=110, target_rooms=3)
    title = "3-комнатная квартира, 80 м²"
    result = analyze_search_result(title, "от 34 901 640 ₸", **criteria)
    assert result["price_kzt"] is None
    assert result["price_lower_bound_kzt"] == 34_901_640
    assert result["price_is_lower_bound"] is True
    assert result["price_per_m2"] is None
    assert "бюджет не подтверждён" in " ".join(result["reasons"])
    assert is_target_search_result(title, "от 34 901 640 ₸", **criteria)
    assert not is_target_search_result(title, "от 50 млн ₸", **criteria)


@pytest.mark.asyncio
async def test_search_results_filters_old_rows_before_limit(monkeypatch):
    from app import main
    from app.models import ListingLike, ListingWorkflow

    now = datetime.now(timezone.utc)
    def row(index, title):
        return SimpleNamespace(id=index, title=title, snippet=None, url=f"https://krisha.kz/a/show/{index}",
                               search_engine="krisha_direct", query="", status="new:normal",
                               import_status="pending", imported_listing_id=None, import_error=None,
                               imported_at=None, first_seen=now, last_seen=now,
                               bathrooms_count=None, bathrooms_count_is_lower_bound=False)

    rows = [row(i, "2-комнатная квартира, 60 м², 25 млн тг") for i in range(200)]
    rows += [row(201, "3-комнатная квартира, 80 м², 40 млн тг")]

    class Scalars:
        def __init__(self, items): self.items = items
        def all(self): return self.items
        def __iter__(self): return iter(self.items)

    batches = []
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def scalars(self, query):
            if (not hasattr(query, "column_descriptions")
                    or query.column_descriptions[0]["entity"] in {ListingLike, ListingWorkflow}):
                return Scalars([])
            offset, limit = query._offset_clause.value, query._limit_clause.value
            batches.append((offset, limit))
            return Scalars(rows[offset:offset + limit])

    monkeypatch.setattr(main, "SessionLocal", Session)
    monkeypatch.setattr(main.settings, "target_rooms", 3)
    monkeypatch.setattr(main.settings, "min_area_m2", 75)
    monkeypatch.setattr(main.settings, "max_area_m2", 110)
    monkeypatch.setattr(main.settings, "max_price_kzt", 45_000_000)
    result = await main.search_results(limit=1)
    assert [item["id"] for item in result] == [201]
    assert batches == [(0, 200), (200, 200)]


@pytest.mark.asyncio
async def test_status_counts_only_results_matching_current_criteria(monkeypatch):
    from app import search_agent

    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(url="https://krisha.kz/a/show/11", title="2-комнатная квартира, 60 м², 25 млн тг", snippet=None,
                        status="new:urgent", import_status="pending", first_seen=now, search_engine="bing"),
        SimpleNamespace(url="https://krisha.kz/a/show/12", title="3-комнатная квартира, 80 м², 40 млн тг", snippet=None,
                        status="new:normal", import_status="imported", first_seen=now, search_engine="krisha_direct"),
    ]

    class AsyncRows:
        def __aiter__(self):
            async def iterate():
                for item in rows:
                    yield item
            return iterate()

    class Scalars:
        def all(self): return []

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def stream_scalars(self, query): return AsyncRows()
        async def execute(self, query): return Scalars()
        async def scalar(self, query): return None
        async def scalars(self, query): return Scalars()

    monkeypatch.setattr(search_agent, "SessionLocal", Session)
    monkeypatch.setattr(search_agent.settings, "target_rooms", 3)
    monkeypatch.setattr(search_agent.settings, "min_area_m2", 75)
    monkeypatch.setattr(search_agent.settings, "max_area_m2", 110)
    monkeypatch.setattr(search_agent.settings, "max_price_kzt", 45_000_000)
    status = await search_agent.get_search_status()
    assert status["total_results"] == status["new_results"] == status["imported_results"] == 1
    assert status["urgent_results"] == 1
    assert status["by_provider"] == {"krisha_direct": 1}
