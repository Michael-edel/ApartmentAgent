from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service, main, search_agent
from app.models import Base, Listing, SearchPreference, SearchResult
from app.pledge import parse_pledge_claim, pledge_field
from app.schema_migrations import ensure_legacy_columns
from app.schemas import ListingCreate
from app.search_providers import SearchItem


PLEDGED_DESCRIPTION = "📄 Квартира в залоге, оформление через банк — всё прозрачно."
TITLE = "4-комнатная квартира · 100 м² · 40 000 000 ₸"


@pytest.mark.parametrize("description,status", [
    (PLEDGED_DESCRIPTION, True),
    ("Данная квартира сейчас находится в залоге.", True),
    ("Продам квартиру в залоге.", True),
    ("Квартира не в залоге.", False),
    ("Квартира не находится в залоге.", False),
    ("Объект находится не в залоге.", False),
])
def test_explicit_pledge_claim_has_literal_evidence(description, status):
    claim = parse_pledge_claim(description)
    assert claim is not None and claim[0] is status
    assert claim[1] in description


@pytest.mark.parametrize("description", [
    None,
    "Ипотека возможна. Оформление через банк.",
    "Квартира в залоге?",
    "Квартира в залоге или нет.",
    "Если квартира в залоге, уточните условия.",
    "Квартира будет в залоге.",
    "Квартира в залоге после получения кредита.",
    "Квартира в залоге не была.",
    "Квартира была в залоге ранее.",
    "Соседняя квартира в залоге.",
    "У соседей квартира в залоге.",
    "Квартира в залоге. Квартира не в залоге.",
])
def test_no_pledge_inference_from_unrelated_or_uncertain_prose(description):
    assert parse_pledge_claim(description) is None


@pytest.mark.parametrize("fields,expected", [
    ({"В залоге": "Да"}, True),
    ({"В залоге": "Нет"}, False),
    ({" В  залоге: ": "нет."}, False),
    ({"В залоге": "1"}, None),
    ({"В залоге": "Не указано"}, None),
    ({"Ипотека": "Да"}, None),
    ({}, None),
])
def test_structured_pledge_is_tristate(fields, expected):
    assert pledge_field(fields) is expected


@pytest_asyncio.fixture
async def pledge_sessions(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        for table, column in (("listings", "is_pledged"), ("search_results", "is_pledged"),
                              ("search_preferences", "exclude_pledged")):
            await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        await connection.run_sync(ensure_legacy_columns)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    for module in (listing_service, search_agent, main):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    for settings in (search_agent.settings, main.settings):
        for field, value in dict(target_rooms=4, max_price_kzt=50_000_000, min_area_m2=80,
                                 max_area_m2=120, min_bathrooms=2, exclude_pledged=True).items():
            monkeypatch.setattr(settings, field, value)

    async def fail_remote(*args, **kwargs):
        pytest.fail("pledge processing must use only the stored normalized data")

    for operation in ("lookup_twogis", "notify_listing", "analyze_listing"):
        monkeypatch.setattr(listing_service, operation, fail_remote)
    yield sessions
    await engine.dispose()


def listing_url(number):
    return f"https://krisha.kz/a/show/{number}"


def payload(number=123, **kwargs):
    return ListingCreate(source="browser", source_url=listing_url(number), title=TITLE,
                         rooms=4, area_m2=100, price_kzt=40_000_000, **kwargs)


def search_row(number, **kwargs):
    return SearchResult(url=listing_url(number), title=TITLE, search_engine="fixture",
                        query="", status="new:urgent", **kwargs)


async def save(value):
    return await listing_service.persist_listing(value, notify=False, refresh_ai=False)


@pytest.mark.asyncio
async def test_pledge_survives_partial_import_and_corrects_all_canonical_results(pledge_sessions):
    async with pledge_sessions() as session:
        session.add_all([
            search_row(123),
            SearchResult(url="https://www.krisha.kz/a/show/123?origin=test", title=TITLE,
                         search_engine="fixture", query="", status="new:normal"),
            search_row(1234),
        ])
        await session.commit()

    first = await save(payload(description=PLEDGED_DESCRIPTION, property_details={"В залоге": "Нет"},
                               details_extracted=True))
    assert first.row.is_pledged is False
    partial = await save(payload(description=PLEDGED_DESCRIPTION))
    assert partial.row.is_pledged is False
    explicit = await save(payload(property_details={"В залоге": "Да"}, details_extracted=True))
    assert explicit.row.is_pledged is True
    final = await save(payload())
    assert final.row.is_pledged is True
    assert final.row.mortgage_supported is None
    async with pledge_sessions() as session:
        rows = (await session.scalars(select(SearchResult).order_by(SearchResult.id))).all()
        assert [row.is_pledged for row in rows] == [True, True, None]
        assert [row.imported_listing_id for row in rows] == [first.row.id, first.row.id, None]
    card = await main._load_listing_response(first.row.id)
    assert card.is_pledged is True and "В залоге" in card.assessment.reasons
    assert len(card.price_history) == 1


@pytest.mark.asyncio
async def test_backfill_pledge_from_saved_description_keeps_import_metadata(pledge_sessions):
    observed_at = datetime(2026, 11, 6, tzinfo=UTC)
    async with pledge_sessions() as session:
        listing = Listing(source_url=listing_url(123), title=TITLE, description=PLEDGED_DESCRIPTION,
                          rooms=4, area_m2=100, price_kzt=40_000_000, last_imported_at=observed_at)
        session.add_all([listing, search_row(123, import_status="blocked"), search_row(1234)])
        await session.commit()
    assert await listing_service.backfill_pledge_status() == 1
    assert await listing_service.backfill_pledge_status() == 0
    async with pledge_sessions() as session:
        card = await session.get(Listing, listing.id)
        results = (await session.scalars(select(SearchResult).order_by(SearchResult.id))).all()
        assert card.is_pledged is True and card.mortgage_supported is None
        assert card.last_imported_at.replace(tzinfo=UTC) == observed_at
        assert card.details_imported_at is None and card.prices == []
        assert [row.is_pledged for row in results] == [True, None]
        assert results[0].import_status == "blocked" and results[0].title == TITLE


@pytest.mark.asyncio
async def test_default_hide_and_preference_toggle_apply_to_search_and_statistics(pledge_sessions, monkeypatch):
    async with pledge_sessions() as session:
        session.add_all([
            search_row(123, is_pledged=True, bathrooms_count=2),
            search_row(124, is_pledged=True),
            search_row(125, is_pledged=False, bathrooms_count=2),
            search_row(126, bathrooms_count=2),
        ])
        await session.commit()
    assert {row["url"] for row in await main.search_results()} == {listing_url(125), listing_url(126)}
    assert await main.search_results(include_unconfirmed=True) == []
    status = await search_agent.get_search_status()
    assert status["total_results"] == 2 and status["pledged_hidden_results"] == 2
    assert status["exclude_pledged"] is True

    async def fixture_search():
        return search_agent.SearchSummary()

    monkeypatch.setattr(search_agent, "_run_search", fixture_search)
    response = await main.run_search_now(main.SearchRunRequest(rooms=4, exclude_pledged=False))
    assert response["exclude_pledged"] is False
    async with pledge_sessions() as session:
        assert (await session.get(SearchPreference, 1)).exclude_pledged is False
    monkeypatch.setattr(search_agent.settings, "exclude_pledged", True)
    await search_agent.load_search_preference()
    results = {row["url"]: row for row in await main.search_results()}
    assert set(results) == {listing_url(123), listing_url(125), listing_url(126)}
    pledged = results[listing_url(123)]["analysis"]
    ordinary = results[listing_url(125)]["analysis"]
    assert pledged["is_pledged"] is True and ordinary["is_pledged"] is False
    assert pledged["score"] == ordinary["score"] - 25
    assert "В залоге" in pledged["reasons"] and "В залоге" not in ordinary["reasons"]
    assert results[listing_url(126)]["analysis"]["is_pledged"] is None
    status = await search_agent.get_search_status()
    assert status["total_results"] == 3 and status["pledged_hidden_results"] == 0
    assert status["urgent_results"] == 2


@pytest.mark.asyncio
async def test_new_search_alias_of_known_pledged_card_stays_hidden_and_unscheduled(pledge_sessions):
    await save(payload(description=PLEDGED_DESCRIPTION, details_extracted=True))
    alias = "https://www.krisha.kz/a/show/123?origin=test"
    new_found, visible, candidates = await search_agent._save_items(
        "fixture", "", [SearchItem(title=TITLE, url=url, snippet=None) for url in (alias, listing_url(124))]
    )
    assert (new_found, visible) == (1, 1)
    assert [candidate.url for candidate in candidates] == [listing_url(124)]
    async with pledge_sessions() as session:
        row = await session.scalar(select(SearchResult).where(SearchResult.url == alias))
        assert row.is_pledged is True
