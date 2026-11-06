from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service
from app.models import Base, Listing, PriceSnapshot
from app.schemas import ListingCreate


GOOD = "Предлагается к продаже полноценная квартира с удобной планировкой и хорошим ремонтом.\n\n* Качественный, хороший ремонт."
NEUTRAL = "Просторная квартира с отдельной кухней. Окна выходят во двор. Документы готовы."
BARE = "Продаётся просторная квартира без ремонта и без мебели. Все документы готовы."


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, "SessionLocal", sessions)

    async def no_remote_enrichment(payload, **kwargs):
        return payload, False, False

    async def forbidden_remote_call(*args, **kwargs):
        raise AssertionError("Repair storage acceptance must not call external integrations")

    monkeypatch.setattr(listing_service, "_enrich_twogis", no_remote_enrichment)
    monkeypatch.setattr(listing_service, "analyze_listing", forbidden_remote_call)
    monkeypatch.setattr(listing_service, "notify_listing", forbidden_remote_call)
    yield SimpleNamespace(sessions=sessions)
    await engine.dispose()


def payload(**kwargs):
    values = dict(source="browser", source_url="https://krisha.kz/a/show/123",
                  title="QA repair storage", price_kzt=40000000, area_m2=100, rooms=4,
                  description=GOOD, details_extracted=True)
    values.update(kwargs)
    return ListingCreate(**values)


async def save(value):
    return await listing_service.persist_listing(value, notify=False, refresh_ai=False)


def repair_tuple(row):
    return row.repair_condition, row.repair_source_kind, row.repair_evidence


@pytest.mark.asyncio
async def test_description_repair_is_saved_with_exact_evidence(env):
    result = await save(payload())
    row = result.row
    assert row.repair_condition == "хороший ремонт"
    assert row.repair_source_kind == "description"
    assert row.repair_evidence in GOOD and len(row.repair_evidence) <= 500
    assert row.details_imported_at and row.description == GOOD
    async with env.sessions() as session:
        saved = await session.get(Listing, row.id)
        assert repair_tuple(saved) == repair_tuple(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [None, "field", "manual"])
async def test_description_cannot_overwrite_trusted_legacy_field_or_manual_repair(env, kind):
    async with env.sessions() as session:
        row = Listing(source="manual" if kind == "manual" else "browser", source_url="https://krisha.kz/a/show/123",
                      title="QA trusted repair", price_kzt=40000000, area_m2=100, rooms=4,
                      repair_condition="требуется ремонт", repair_source_kind=kind,
                      repair_evidence=None, description=NEUTRAL)
        session.add(row)
        await session.commit()
    changed = await save(payload(description=GOOD))
    assert repair_tuple(changed.row) == ("требуется ремонт", kind, None)
    assert changed.row.description == GOOD


@pytest.mark.asyncio
async def test_short_metadata_preserves_full_description_and_derived_repair(env):
    first = await save(payload(photo_urls=["https://photos.example/one.jpg", "https://photos.example/two.jpg"]))
    original = repair_tuple(first.row)
    stamp = first.row.details_imported_at
    updated = await save(payload(description="Короткие метаданные: без ремонта.", details_extracted=False,
                                 price_kzt=39000000, photo_urls=["https://photos.example/preview.jpg"]))
    assert repair_tuple(updated.row) == original
    assert updated.row.description == GOOD
    assert updated.row.details_imported_at == stamp
    assert updated.row.photo_urls == ["https://photos.example/one.jpg", "https://photos.example/two.jpg"]
    async with env.sessions() as session:
        snapshots = (await session.scalars(select(PriceSnapshot).where(PriceSnapshot.listing_id == first.row.id))).all()
        assert sorted(row.price_kzt for row in snapshots) == [39000000, 40000000]


@pytest.mark.asyncio
async def test_full_new_description_changes_then_clears_all_derived_repair_fields(env):
    await save(payload())
    changed = await save(payload(description=BARE))
    assert changed.row.repair_condition == "без ремонта" and changed.row.repair_source_kind == "description"
    assert changed.row.repair_evidence in BARE
    cleared = await save(payload(description=NEUTRAL))
    assert repair_tuple(cleared.row) == (None, None, None)
    assert cleared.row.description == NEUTRAL


@pytest.mark.asyncio
async def test_explicit_field_replaces_derived_repair_and_clears_its_quote(env):
    await save(payload())
    changed = await save(payload(repair_condition="евроремонт"))
    assert repair_tuple(changed.row) == ("евроремонт", "field", None)
    preserved = await save(payload(description=BARE))
    assert repair_tuple(preserved.row) == ("евроремонт", "field", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("source,kind", [("browser", "field"), ("manual", "manual")])
async def test_labelled_field_beats_prose_and_manual_origin_is_not_attributed_to_seller(env, source, kind):
    labelled = await save(payload(source=source, property_details={"Состояние квартиры": "требуется ремонт"}))
    assert repair_tuple(labelled.row) == ("требуется ремонт", kind, None)
    explicit = await save(payload(source=source, repair_condition="евроремонт",
                                  property_details={"Состояние квартиры": "требуется ремонт"}))
    assert repair_tuple(explicit.row) == ("евроремонт", kind, None)


@pytest.mark.asyncio
async def test_derived_input_is_rechecked_against_new_prose_instead_of_trusting_stale_quote(env):
    result = await save(payload(description=BARE, repair_condition="хороший ремонт",
                                repair_source_kind="description", repair_evidence="хороший ремонт"))
    assert result.row.repair_condition == "без ремонта" and result.row.repair_source_kind == "description"
    assert result.row.repair_evidence in BARE


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["2", "2.0"])
@pytest.mark.parametrize("description,expected", [(GOOD, "хороший ремонт"), (NEUTRAL, None)])
async def test_numeric_repair_codes_are_not_translated_into_human_quality(env, code, description, expected):
    result = await save(payload(description=description, repair_condition=code,
                                property_details={"Состояние квартиры": code}))
    assert result.row.property_details["Состояние квартиры"] == code
    if expected:
        assert result.row.repair_condition == expected and result.row.repair_source_kind == "description"
        assert result.row.repair_evidence in description
    else:
        assert repair_tuple(result.row) == (None, None, None)


@pytest.mark.asyncio
async def test_startup_backfill_is_local_idempotent_and_does_not_change_price_history(env):
    stamp = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    specifications = [
        (GOOD, None, None, None, {}),
        (GOOD, "требуется ремонт", None, None, {}),
        (GOOD, "требуется ремонт", "manual", None, {}),
        (NEUTRAL, "хороший ремонт", "description", "старое свидетельство", {}),
        (NEUTRAL, None, None, None, {}),
        (GOOD, None, None, None, {"Ремонт": "черновая отделка"}),
    ]
    ids = []
    async with env.sessions() as session:
        for number, (description, condition, kind, evidence, details) in enumerate(specifications, 1):
            row = Listing(source="browser", source_url=f"https://krisha.kz/a/show/{100 + number}",
                          title=f"QA legacy repair {number}", price_kzt=40000000, area_m2=100, rooms=4,
                          description=description, repair_condition=condition, repair_source_kind=kind,
                          repair_evidence=evidence, property_details=details, last_imported_at=stamp)
            session.add(row)
            await session.flush()
            ids.append(row.id)
            session.add(PriceSnapshot(listing_id=row.id, price_kzt=row.price_kzt, price_per_m2=400000))
        await session.commit()
        before_snapshots = (await session.execute(select(PriceSnapshot.listing_id, PriceSnapshot.price_kzt, PriceSnapshot.observed_at))).all()

    assert await listing_service.backfill_repair_descriptions() == 3
    assert await listing_service.backfill_repair_descriptions() == 0
    async with env.sessions() as session:
        rows = [(await session.get(Listing, row_id)) for row_id in ids]
        assert repair_tuple(rows[0])[:2] == ("хороший ремонт", "description") and rows[0].repair_evidence in GOOD
        assert repair_tuple(rows[1]) == ("требуется ремонт", None, None)
        assert repair_tuple(rows[2]) == ("требуется ремонт", "manual", None)
        assert repair_tuple(rows[3]) == repair_tuple(rows[4]) == (None, None, None)
        assert repair_tuple(rows[5]) == ("черновая отделка", "field", None)
        assert all(row.price_kzt == 40000000 for row in rows)
        assert all(row.last_imported_at.replace(tzinfo=UTC) == stamp for row in rows)
        after_snapshots = (await session.execute(select(PriceSnapshot.listing_id, PriceSnapshot.price_kzt, PriceSnapshot.observed_at))).all()
        assert after_snapshots == before_snapshots


@pytest.mark.asyncio
async def test_cached_manual_provenance_survives_a_source_change(env):
    result = await save(payload(repair_condition="евроремонт", repair_source_kind="manual"))
    assert repair_tuple(result.row) == ("евроремонт", "manual", None)
