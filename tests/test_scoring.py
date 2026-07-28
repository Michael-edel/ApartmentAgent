from app.config import Settings
from app.scoring import assess_listing
from app.schemas import ListingCreate


def test_target_listing_scores_high() -> None:
    listing = ListingCreate(
        source_url="https://example.com/listing/1",
        title="Полноценная 2-комнатная квартира",
        price_kzt=29_000_000,
        area_m2=60,
        rooms=2,
        floor=4,
        floors_total=10,
        building_year=2020,
        building_type="монолит",
        mortgage_supported=True,
    )

    result = assess_listing(listing, Settings())

    assert result.score >= 90
    assert result.verdict == "ПОКУПАТЬ"
    assert result.price_per_m2 == 483_333


def test_first_floor_and_wrong_area_reduce_score() -> None:
    listing = ListingCreate(
        source_url="https://example.com/listing/2",
        title="Компактная квартира",
        price_kzt=31_000_000,
        area_m2=45,
        rooms=2,
        floor=1,
        floors_total=9,
        building_type="панель",
    )

    result = assess_listing(listing, Settings())

    assert result.score < 75
    assert result.verdict == "НЕ РЕКОМЕНДУЮ"
