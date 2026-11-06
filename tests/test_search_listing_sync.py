from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service
from app.models import Base, Listing, SearchResult
from app.schemas import ListingCreate


@pytest.mark.asyncio
async def test_manual_save_confirms_search_bathrooms_without_losing_partial_data(monkeypatch):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, "SessionLocal", sessions)
    async with sessions() as session:
        for source_url in (
            "https://krisha.kz/a/show/123",
            "https://www.krisha.kz/a/show/123?from=search",
            "https://krisha.kz/a/show/1234",
        ):
            session.add(SearchResult(url=source_url, title="Тест", query="test",
                                     search_engine="test", status="new:normal",
                                     import_status="blocked", import_error="blocked"))
        await session.commit()
    payload = ListingCreate(source="manual", source_url="https://krisha.kz/a/show/123?x=1",
                            title="Тестовая квартира", price_kzt=45_000_000, area_m2=100,
                            rooms=4, bathrooms_count=2, bathrooms_count_is_lower_bound=True)
    saved = await listing_service.persist_listing(payload, notify=False, refresh_ai=False)
    partial = payload.model_copy(update={"bathrooms_count": None,
                                          "bathrooms_count_is_lower_bound": False})
    await listing_service.persist_listing(partial, notify=False, refresh_ai=False)
    async with sessions() as session:
        for result_id in (1, 2):
            result = await session.get(SearchResult, result_id)
            assert result.imported_listing_id == saved.row.id
            assert result.import_status == "imported"
            assert result.import_error is None
            assert result.imported_at is not None
            assert result.bathrooms_count == 2
            assert result.bathrooms_count_is_lower_bound is True
        unrelated = await session.get(SearchResult, 3)
        assert unrelated.import_status == "blocked"
        assert unrelated.bathrooms_count is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_existing_listing_does_not_change_listing_or_price_history(monkeypatch):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, "SessionLocal", sessions)
    payload = ListingCreate(source_url="https://krisha.kz/a/show/123", title="Тест",
                            price_kzt=45_000_000, area_m2=100, rooms=4,
                            bathrooms_count=2, bathrooms_count_is_lower_bound=True)
    saved = await listing_service.persist_listing(payload, notify=False, refresh_ai=False)
    async with sessions() as session:
        session.add(SearchResult(url="https://krisha.kz/a/show/123", title="Тест", query="test",
                                 search_engine="test", status="new:normal", import_status="blocked"))
        await session.commit()
        row = await session.get(Listing, saved.row.id)
        old_imported_at, old_price = row.last_imported_at, row.price_kzt
        await listing_service._sync_search_results(session, row, datetime.now(UTC))
        await session.commit()
        assert row.last_imported_at == old_imported_at
        assert row.price_kzt == old_price
        await session.refresh(row, ["prices"])
        assert len(row.prices) == 1
        result = await session.get(SearchResult, 1)
        assert result.bathrooms_count == 2
        assert result.imported_listing_id == row.id
    await engine.dispose()
