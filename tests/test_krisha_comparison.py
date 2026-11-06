from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service
from app.main import _payload_from_row, _to_response
from app.market_comparison import compare_saved_listings
from app.models import Base
from app.schemas import ListingCreate


def test_krisha_references_are_separate_from_saved_sample_and_observation_date():
    observed = datetime(2026, 11, 6, 10, tzinfo=UTC)
    target = SimpleNamespace(
        id=1, source_url='https://krisha.kz/a/show/1', city='Астана', district='Нура',
        price_kzt=25_000_000, area_m2=60, last_imported_at=observed,
        krisha_district_price_per_m2=845_700, krisha_city_price_per_m2=792_420,
        krisha_comparison_observed_at=observed, krisha_comparison_source='krisha_listing_page',
        krisha_comparison_district='Есильском р-н',
    )
    result = compare_saved_listings(target, [], now=observed)
    assert result['city']['status'] == 'insufficient_data'
    assert result['krisha']['district']['average_price_per_m2'] == 845_700
    assert result['krisha']['district']['difference_percent'] == -50.7
    assert result['krisha']['city']['difference_percent'] == -47.4
    assert result['krisha']['district_label'] == 'Есильском р-н'
    assert result['krisha']['observed_at'] == observed.isoformat()
    assert result['krisha']['period'] is None


@pytest.mark.asyncio
async def test_krisha_snapshot_survives_absent_block_and_partial_refresh_is_atomic(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(listing_service, 'SessionLocal', async_sessionmaker(engine, expire_on_commit=False))
    payload = ListingCreate(
        source_url='https://krisha.kz/a/show/123', title='Тест', price_kzt=25_000_000,
        area_m2=60, rooms=3, krisha_district_price_per_m2=845_700,
        krisha_city_price_per_m2=792_420, krisha_comparison_district='Есильском р-н',
    )
    saved = await listing_service.persist_listing(payload, notify=False, refresh_ai=False)
    observed = saved.row.krisha_comparison_observed_at
    absent = payload.model_copy(update={'krisha_district_price_per_m2': None,
                                       'krisha_city_price_per_m2': None,
                                       'krisha_comparison_district': None})
    unchanged = await listing_service.persist_listing(absent, notify=False, refresh_ai=False)
    assert unchanged.row.krisha_comparison_observed_at == observed
    assert unchanged.row.krisha_district_price_per_m2 == 845_700
    assert _payload_from_row(unchanged.row).krisha_city_price_per_m2 == 792_420
    response = _to_response(unchanged.row)
    assert response.krisha_comparison_source == 'krisha_listing_page'
    assert response.krisha_comparison_observed_at == observed

    partial = absent.model_copy(update={'krisha_city_price_per_m2': 800_000})
    refreshed = await listing_service.persist_listing(partial, notify=False, refresh_ai=False)
    assert refreshed.row.krisha_district_price_per_m2 is None
    assert refreshed.row.krisha_comparison_district is None
    assert refreshed.row.krisha_city_price_per_m2 == 800_000
    assert refreshed.row.krisha_comparison_observed_at >= observed
    await engine.dispose()


def test_krisha_references_require_positive_values_and_krisha_source():
    base = dict(source_url='https://krisha.kz/a/show/1', title='Тест',
                price_kzt=25_000_000, area_m2=60, rooms=3)
    with pytest.raises(ValidationError):
        ListingCreate(**base, krisha_city_price_per_m2=0)
    with pytest.raises(ValidationError):
        ListingCreate(**{**base, 'source_url': 'https://example.com/1'}, krisha_city_price_per_m2=792_420)
