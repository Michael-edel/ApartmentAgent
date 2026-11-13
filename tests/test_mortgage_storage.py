"""Local storage fixtures only; no source page or external integration is used."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service
from app.models import Base, Listing, PriceSnapshot, SearchResult
from app.schemas import ListingCreate


URL = "https://krisha.kz/a/show/123"
YES = "Продаётся просторная квартира. Можно под ипотеку. Документы готовы."
NO = "Квартира продаётся без мебели. Ипотеки нет."
NEUTRAL = "Просторная квартира с отдельной кухней. Окна выходят во двор."
CONSULTATION = "Бесплатная консультация по ипотеке. Поможем рассчитать ежемесячный платёж."
TITLE_YES = "4-комнатная квартира · 100 м². Можно в ИПОТЕКУ"


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, "SessionLocal", sessions)

    async def no_remote_enrichment(value, **kwargs):
        return value, False, False

    async def forbidden_remote_call(*args, **kwargs):
        raise AssertionError("Mortgage storage acceptance must not call external integrations")

    monkeypatch.setattr(listing_service, "_enrich_twogis", no_remote_enrichment)
    monkeypatch.setattr(listing_service, "analyze_listing", forbidden_remote_call)
    monkeypatch.setattr(listing_service, "notify_listing", forbidden_remote_call)
    yield SimpleNamespace(sessions=sessions)
    await engine.dispose()


def payload(**kwargs):
    values = dict(source="browser", source_url=URL, title="QA mortgage storage",
                  price_kzt=40000000, area_m2=100, rooms=4,
                  description=YES, details_extracted=True)
    values.update(kwargs)
    return ListingCreate(**values)


async def save(value):
    return await listing_service.persist_listing(value, notify=False, refresh_ai=False)


def mortgage_tuple(row):
    return row.mortgage_supported, row.mortgage_source_kind, row.mortgage_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("description,expected", [(YES, True), (NO, False), ("Оплата исключительно наличными.", False)])
async def test_explicit_prose_is_saved_with_its_exact_evidence(env, description, expected):
    result = await save(payload(description=description))
    assert result.row.mortgage_supported is expected
    assert result.row.mortgage_source_kind == "description"
    assert result.row.mortgage_evidence in description
    assert len(result.row.mortgage_evidence) <= 500
    async with env.sessions() as session:
        stored = await session.get(Listing, result.row.id)
        assert mortgage_tuple(stored) == mortgage_tuple(result.row)


@pytest.mark.asyncio
@pytest.mark.parametrize("description", [NEUTRAL, CONSULTATION, "Можно в ипотеку. Только наличный расчёт."])
async def test_absence_services_and_ambiguous_payment_options_stay_unknown(env, description):
    result = await save(payload(description=description))
    assert mortgage_tuple(result.row) == (None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [None, "field", "manual"])
async def test_prose_cannot_overwrite_a_trusted_field_or_manual_false(env, kind):
    async with env.sessions() as session:
        session.add(Listing(source="manual" if kind == "manual" else "browser", source_url=URL,
                            title="QA trusted mortgage", price_kzt=40000000, area_m2=100, rooms=4,
                            mortgage_supported=False, mortgage_source_kind=kind, description=NEUTRAL))
        await session.commit()
    updated = await save(payload())
    assert mortgage_tuple(updated.row) == (False, kind, None)


@pytest.mark.asyncio
async def test_explicit_field_replaces_text_derivation_and_clears_its_quote(env):
    await save(payload())
    changed = await save(payload(mortgage_supported=False))
    assert mortgage_tuple(changed.row) == (False, "field", None)
    unchanged = await save(payload(description=YES))
    assert mortgage_tuple(unchanged.row) == (False, "field", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("source,kind", [("browser", "field"), ("manual", "manual")])
async def test_property_field_and_manual_value_have_priority_over_prose(env, source, kind):
    labelled = await save(payload(source=source, property_details={"Ипотека": "Нет"}))
    assert mortgage_tuple(labelled.row) == (False, kind, None)
    explicit = await save(payload(source=source, mortgage_supported=True,
                                  property_details={"Ипотека": "Нет"}, description=NO))
    assert mortgage_tuple(explicit.row) == (True, kind, None)


@pytest.mark.asyncio
async def test_short_metadata_keeps_full_description_photos_and_mortgage(env):
    first = await save(payload(photo_urls=["https://photos.example/one.jpg", "https://photos.example/two.jpg"]))
    original, stamp = mortgage_tuple(first.row), first.row.details_imported_at
    updated = await save(payload(description=NO, details_extracted=False, price_kzt=39000000,
                                 photo_urls=["https://photos.example/preview.jpg"]))
    assert mortgage_tuple(updated.row) == original
    assert updated.row.description == YES and updated.row.details_imported_at == stamp
    assert updated.row.photo_urls == ["https://photos.example/one.jpg", "https://photos.example/two.jpg"]
    async with env.sessions() as session:
        snapshots = (await session.scalars(select(PriceSnapshot).where(PriceSnapshot.listing_id == first.row.id))).all()
        assert sorted(row.price_kzt for row in snapshots) == [39000000, 40000000]


@pytest.mark.asyncio
async def test_full_text_changes_then_clears_derived_value_and_search_fact(env):
    async with env.sessions() as session:
        session.add(SearchResult(url="https://www.krisha.kz/a/show/123?from=fixture", title=TITLE_YES,
                                 search_engine="fixture", query="fixture", mortgage_supported=True))
        await session.commit()
    await save(payload())
    refused = await save(payload(description=NO))
    assert refused.row.mortgage_supported is False and refused.row.mortgage_evidence in NO
    async with env.sessions() as session:
        assert (await session.scalar(select(SearchResult))).mortgage_supported is False
    cleared = await save(payload(description=NEUTRAL))
    assert mortgage_tuple(cleared.row) == (None, None, None)
    async with env.sessions() as session:
        search = await session.scalar(select(SearchResult))
        assert search.mortgage_supported is None
        assert search.imported_listing_id == cleared.row.id


@pytest.mark.asyncio
async def test_actual_title_claim_is_not_lost_because_description_advertises_consultation(env):
    result = await save(payload(title=TITLE_YES, description=CONSULTATION))
    assert result.row.mortgage_supported is True and result.row.mortgage_source_kind == "title"
    assert result.row.mortgage_evidence in TITLE_YES
    assert result.row.mortgage_evidence not in CONSULTATION


@pytest.mark.asyncio
async def test_a_title_claim_after_maximum_length_description_is_still_examined(env):
    description = ("Окна выходят во двор. " * 1200)[:20000]
    result = await save(payload(title=TITLE_YES, description=description))
    assert result.row.mortgage_supported is True and result.row.mortgage_source_kind == "title"
    assert result.row.mortgage_evidence in TITLE_YES


@pytest.mark.asyncio
async def test_derived_client_value_is_rechecked_instead_of_trusting_stale_evidence(env):
    result = await save(payload(description=NO, mortgage_supported=True,
                                mortgage_source_kind="description", mortgage_evidence="Можно в ипотеку"))
    assert result.row.mortgage_supported is False and result.row.mortgage_evidence in NO


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["description", "title"])
async def test_metadata_preserves_legacy_text_derivation_without_detail_timestamp(env, kind):
    title, description = (TITLE_YES, None) if kind == "title" else ("QA legacy mortgage", YES)
    evidence = "Можно в ИПОТЕКУ" if kind == "title" else "Можно под ипотеку."
    async with env.sessions() as session:
        session.add(Listing(source="browser", source_url=URL, title=title, description=description,
                            price_kzt=40000000, area_m2=100, rooms=4, mortgage_supported=True,
                            mortgage_source_kind=kind, mortgage_evidence=evidence, details_imported_at=None))
        await session.commit()
    updated = await save(payload(title="QA short metadata", description=NO, details_extracted=False))
    assert mortgage_tuple(updated.row) == (True, kind, evidence)
    assert updated.row.description == description
    if kind == "title":
        assert updated.row.title == title
    cleared = await save(payload(title="QA neutral full title", description=None if kind == "title" else NEUTRAL,
                                 details_extracted=True))
    assert mortgage_tuple(cleared.row) == (None, None, None)


@pytest.mark.asyncio
async def test_cached_manual_provenance_is_preserved(env):
    result = await save(payload(mortgage_supported=True, mortgage_source_kind="manual", description=NO))
    assert mortgage_tuple(result.row) == (True, "manual", None)


@pytest.mark.asyncio
async def test_local_backfill_is_idempotent_cleans_floor_and_preserves_price_history(env):
    stamp = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    specs = [
        (YES, "QA legacy", None, None, None, {}, "9 этаж, Ул.Қаныш Сәтбаев , дом 33, 33/1 33"),
        (YES, "QA trusted", False, None, None, {}, "9 Мая 17"),
        (NO, "QA manual", True, "manual", None, {}, None),
        (NEUTRAL, "QA stale", True, "description", "старое свидетельство", {}, None),
        (NEUTRAL, "QA unknown", None, None, None, {}, None),
        (YES, "QA field", None, None, None, {"Ипотека": "Нет"}, None),
        (CONSULTATION, TITLE_YES, None, None, None, {}, None),
    ]
    ids = []
    async with env.sessions() as session:
        for number, (description, title, value, kind, evidence, details, address) in enumerate(specs, 1):
            row = Listing(source="browser", source_url=f"https://krisha.kz/a/show/{200 + number}",
                          title=title, description=description, price_kzt=40000000, area_m2=100, rooms=4,
                          mortgage_supported=value, mortgage_source_kind=kind, mortgage_evidence=evidence,
                          property_details=details, address=address, last_imported_at=stamp)
            session.add(row)
            await session.flush()
            ids.append(row.id)
            session.add(PriceSnapshot(listing_id=row.id, price_kzt=40000000, price_per_m2=400000))
            session.add(SearchResult(url=row.source_url, title=title, query="fixture", search_engine="fixture",
                                     mortgage_supported=value, imported_listing_id=row.id))
        await session.commit()
        before = (await session.execute(select(PriceSnapshot.id, PriceSnapshot.price_kzt, PriceSnapshot.observed_at)
                                        .order_by(PriceSnapshot.id))).all()

    assert await listing_service.backfill_mortgage_and_addresses() == {"mortgage": 4, "address": 1}
    assert await listing_service.backfill_mortgage_and_addresses() == {"mortgage": 0, "address": 0}
    async with env.sessions() as session:
        rows = [(await session.get(Listing, row_id)) for row_id in ids]
        assert rows[0].mortgage_supported is True and rows[0].mortgage_evidence in YES
        assert rows[0].address == "Ул.Қаныш Сәтбаев , дом 33, 33/1 33"
        assert mortgage_tuple(rows[1]) == (False, None, None) and rows[1].address == "9 Мая 17"
        assert mortgage_tuple(rows[2]) == (True, "manual", None)
        assert mortgage_tuple(rows[3]) == mortgage_tuple(rows[4]) == (None, None, None)
        assert mortgage_tuple(rows[5]) == (False, "field", None)
        assert rows[6].mortgage_supported is True and rows[6].mortgage_source_kind == "title"
        facts = (await session.scalars(select(SearchResult).order_by(SearchResult.id))).all()
        assert facts[0].mortgage_supported is True and facts[3].mortgage_supported is None
        assert all(row.price_kzt == 40000000 and row.last_imported_at.replace(tzinfo=UTC) == stamp for row in rows)
        after = (await session.execute(select(PriceSnapshot.id, PriceSnapshot.price_kzt, PriceSnapshot.observed_at)
                                       .order_by(PriceSnapshot.id))).all()
        assert before == after
