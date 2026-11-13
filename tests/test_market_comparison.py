from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.market_comparison import compare_saved_listings

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def listing(id: int, **overrides):
    values = dict(
        id=id, source_url=f"https://krisha.kz/a/show/{id}", city="Астана",
        district="Есильский район", price_kzt=40_000_000, area_m2=100.0,
        last_imported_at=NOW - timedelta(days=5),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_means_exclude_target_stale_other_city_and_legacy_duplicate_urls():
    target = listing(1, price_kzt=30_000_000)
    rows = [listing(i, district="Есильский р-н") for i in range(2, 7)]
    rows += [
        target,
        listing(7, source_url="https://www.krisha.kz/a/show/2?utm_source=old", price_kzt=90_000_000, last_imported_at=NOW - timedelta(days=10)),
        listing(8, source_url="https://www.krisha.kz/a/show/1?from=search"),
        listing(9, last_imported_at=NOW - timedelta(days=91)),
        listing(10, city="Алматы"),
        listing(11, last_imported_at=None),
    ]
    result = compare_saved_listings(target, rows, now=NOW)
    assert result["city"] == {"count": 5, "average_price_per_m2": 400_000, "difference_percent": -25.0, "status": "ok"}
    assert result["district"] == result["city"]
    assert result["source"] == "saved_listings"


def test_not_enough_values_are_not_displayed_and_unknown_district_is_explicit():
    result = compare_saved_listings(listing(1, district=None), [listing(i) for i in range(2, 6)], now=NOW)
    assert result["city"]["count"] == 4
    assert result["city"]["average_price_per_m2"] is None
    assert result["city"]["status"] == "insufficient_data"
    assert result["district"]["status"] == "district_unknown"


def test_other_city_target_is_not_compared_to_astana():
    result = compare_saved_listings(listing(1, city="Алматы"), [listing(i) for i in range(2, 7)], now=NOW)
    assert result["city_label"] == "Алматы"
    assert result["city"]["count"] == result["district"]["count"] == 0
    assert result["city"]["status"] == "insufficient_data"
    rows = [listing(i, city="Алматы") for i in range(8, 13)]
    assert compare_saved_listings(listing(1, city="Алматы"), rows, now=NOW)["city"]["count"] == 5


def test_five_districts_city_only_and_latest_duplicate_wins():
    rows = [listing(i, district="Нура", price_kzt=20_000_000) for i in range(2, 7)]
    rows.append(listing(7, district="Нуринский район", price_kzt=60_000_000))
    result = compare_saved_listings(listing(1), rows, now=NOW)
    assert result["city"]["count"] == 6
    assert result["city"]["average_price_per_m2"] == 266_667
    assert result["district"]["status"] == "insufficient_data"


def test_district_name_variants_share_one_sample():
    for target_district, sample_district in [
        ("Байконыр", "Байконурский район"),
        ("Есіл", "Есильский р-н"),
    ]:
        rows = [listing(i, district=sample_district) for i in range(2, 7)]
        result = compare_saved_listings(listing(1, district=target_district), rows, now=NOW)
        assert result["district"]["count"] == 5
        assert result["district"]["average_price_per_m2"] == 400_000
        assert result["district"]["status"] == "ok"
