from app.config import Settings
from app.schemas import ListingCreate
from app.scoring import assess_listing
from app.twogis import parse_twogis_response


def test_parse_twogis_response_selects_matching_complex_and_reviews() -> None:
    result = parse_twogis_response(
        {
            "result": {
                "items": [
                    {
                        "name": "ЖК Северный",
                        "type": "building",
                        "point": {"lon": 71.2, "lat": 51.2},
                        "reviews": {"general_rating": "3.9", "review_count": "7"},
                    },
                    {
                        "name": "ЖК Меркурий",
                        "type": "building",
                        "point": {"lon": 71.430, "lat": 51.128},
                        "reviews": {"general_rating": "4.73", "review_count": "32"},
                    },
                ]
            }
        },
        "Меркурий",
        latitude=51.128,
        longitude=71.430,
    )

    assert result is not None
    assert result.name == "ЖК Меркурий"
    assert result.rating == 4.73
    assert result.review_count == 32
    assert result.url.endswith("/search/%D0%96%D0%9A%20%D0%9C%D0%B5%D1%80%D0%BA%D1%83%D1%80%D0%B8%D0%B9")


def test_parse_twogis_response_rejects_unrelated_result() -> None:
    result = parse_twogis_response(
        {"result": {"items": [{"name": "ЖК Береке", "type": "building"}]}},
        "Меркурий",
    )

    assert result is None


def test_twogis_rating_changes_assessment() -> None:
    base = ListingCreate(
        source_url="https://example.com/listing/twogis",
        title="2-комнатная квартира",
        price_kzt=28_000_000,
        area_m2=60,
        rooms=2,
        floor=4,
        floors_total=10,
        building_year=2015,
        building_type="brick",
        twogis_name="ЖК Меркурий",
        twogis_review_count=32,
    )
    good = base.model_copy(update={"twogis_rating": 4.73})
    bad = base.model_copy(update={"twogis_rating": 2.8})

    good_assessment = assess_listing(good, Settings())
    bad_assessment = assess_listing(bad, Settings())

    assert good_assessment.score > bad_assessment.score
    assert any("2GIS" in reason for reason in good_assessment.reasons)
    assert any("Низкая оценка ЖК" in reason for reason in bad_assessment.reasons)
