import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service
from app.author import OWNER_FROM_DESCRIPTION
from app.main import _payload_from_row
from app.models import Base, Listing
from app.schemas import ListingCreate


@pytest.mark.asyncio
async def test_seller_and_mortgage_survive_partial_reimport(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, 'SessionLocal', sessions)
    payload = ListingCreate(source_url='https://krisha.kz/a/show/123', title='Тест',
                            price_kzt=25000000, area_m2=65, rooms=3,
                            seller_name='Алия', seller_type='Хозяин недвижимости',
                            mortgage_supported=False)
    saved = await listing_service.persist_listing(payload, notify=False, refresh_ai=False)
    partial = payload.model_copy(update={'seller_name': None, 'seller_type': None,
                                         'mortgage_supported': None})
    await listing_service.persist_listing(partial, notify=False, refresh_ai=False)
    async with sessions() as session:
        row = await session.get(Listing, saved.row.id)
        response = _payload_from_row(row)
        assert (response.seller_name, response.seller_type, response.mortgage_supported) == (
            'Алия', 'Хозяин недвижимости', False
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_owner_claim_backfill_and_reimport_never_guess_name_or_replace_explicit_type(monkeypatch):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, "SessionLocal", sessions)
    description = "Прямая продажа от хозяев, без посредников."
    url = "https://krisha.kz/a/show/123"
    payload = ListingCreate(source="browser", source_url=url, title="Квартира", price_kzt=25_000_000,
                            area_m2=65, rooms=3, description=description, details_extracted=True)

    async with sessions() as session:
        older = Listing(source_url=url, title=payload.title, description=description,
                        price_kzt=payload.price_kzt, area_m2=payload.area_m2, rooms=payload.rooms)
        session.add(older)
        await session.commit()
    assert await listing_service.backfill_author_types() == 1
    assert await listing_service.backfill_author_types() == 0
    async with sessions() as session:
        row = await session.get(Listing, older.id)
        assert row.seller_type == OWNER_FROM_DESCRIPTION and row.seller_name is None
        assert row.details_imported_at is None and row.last_imported_at is None

    async def save(**changes):
        return (await listing_service.persist_listing(
            payload.model_copy(update=changes), notify=False, refresh_ai=False
        )).row

    partial = await save(description="Продажа квартиры", details_extracted=False)
    assert partial.seller_type == OWNER_FROM_DESCRIPTION and partial.description == description
    missing_description = await save(description=None)
    assert missing_description.seller_type == OWNER_FROM_DESCRIPTION
    neutral = await save(description="Продаётся квартира с просторной гостиной.")
    assert neutral.seller_type is None and neutral.seller_name is None
    derived = await save()
    assert derived.seller_type == OWNER_FROM_DESCRIPTION
    explicit = await save(seller_type="Специалист по недвижимости", seller_name="Имя из карточки")
    assert explicit.seller_type == "Специалист по недвижимости"
    reimport = await save()
    assert reimport.seller_type == explicit.seller_type and reimport.seller_name == "Имя из карточки"
    await engine.dispose()
