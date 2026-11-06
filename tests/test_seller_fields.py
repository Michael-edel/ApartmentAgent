import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service
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
