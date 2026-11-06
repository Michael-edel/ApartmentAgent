import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service
from app.main import _payload_from_row
from app.models import Base, Listing
from app.schema_migrations import ensure_legacy_columns
from app.schemas import ListingCreate


def payload(**updates):
    return ListingCreate(**{
        'source_url': 'https://krisha.kz/a/show/123', 'title': 'Квартира',
        'price_kzt': 25000000, 'area_m2': 65, 'rooms': 3,
        'residential_complex': 'Тестовый ЖК', **updates,
    })


def test_complex_description_requires_provenance_and_is_separate():
    assert payload(description='Текст о квартире').complex_description is None
    with pytest.raises(ValidationError):
        payload(complex_description='Двор и инфраструктура')
    with pytest.raises(ValidationError):
        payload(complex_description='Двор', complex_description_source_kind='developer')
    with pytest.raises(ValidationError):
        payload(complex_description='Двор', complex_description_source_kind='manual',
                complex_description_source_url='javascript:alert(1)')
    assert payload(complex_description='Мой текст', complex_description_source_kind='manual')


def test_complex_description_legacy_migration():
    engine = create_engine('sqlite:///:memory:')
    columns = ['complex_description', 'complex_description_source_url',
               'complex_description_source_kind', 'complex_description_observed_at']
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        for column in columns:
            connection.execute(text(f'ALTER TABLE listings DROP COLUMN {column}'))
        ensure_legacy_columns(connection)
        actual = {column['name'] for column in inspect(connection).get_columns('listings')}
        assert set(columns) <= actual
    engine.dispose()


@pytest.mark.asyncio
async def test_complex_description_snapshot_and_partial_import(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, 'SessionLocal', sessions)
    first = payload(complex_description='Описание из объявления',
                    complex_description_source_kind='listing',
                    complex_description_source_url='https://krisha.kz/a/show/123')
    saved = await listing_service.persist_listing(first, notify=False, refresh_ai=False)
    timestamp = saved.row.complex_description_observed_at
    assert timestamp is not None
    await listing_service.persist_listing(payload(), notify=False, refresh_ai=False)
    async with sessions() as session:
        row = await session.get(Listing, saved.row.id)
        assert row.complex_description_observed_at == timestamp
        assert _payload_from_row(row).complex_description == 'Описание из объявления'
    changed = payload(complex_description='Мой текст', complex_description_source_kind='manual')
    saved = await listing_service.persist_listing(changed, notify=False, refresh_ai=False)
    assert saved.row.complex_description_source_url is None
    assert saved.row.complex_description_source_kind == 'manual'
    saved = await listing_service.persist_listing(payload(residential_complex='Другой ЖК'),
                                                  notify=False, refresh_ai=False)
    assert saved.row.complex_description is None
    assert saved.row.complex_description_observed_at is None
    await engine.dispose()
