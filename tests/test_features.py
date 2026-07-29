from app.ai_service import build_local_analysis
from app.config import Settings
from app.importer import _find_image_urls
from app.schemas import ListingCreate
from app.scoring import assess_listing


def _listing(**overrides: object) -> ListingCreate:
    values: dict[str, object] = {
        "source_url": "https://krisha.kz/a/show/123",
        "title": "2-комнатная квартира",
        "price_kzt": 28_000_000,
        "area_m2": 60,
        "rooms": 2,
        "floor": 4,
        "floors_total": 10,
        "building_year": 2020,
        "building_type": "monolith",
        "photo_urls": ["https://cdn.example.com/flat.jpg"],
    }
    values.update(overrides)
    return ListingCreate(**values)


def test_local_ai_analysis_contains_strengths_and_next_steps() -> None:
    listing = _listing()
    assessment = assess_listing(listing, Settings())

    result = build_local_analysis(listing, assessment)

    assert result["provider"] == "local-rules-ai"
    assert result["strengths"]
    assert result["next_steps"]
    assert result["recommendation"] == assessment.verdict


def test_local_ai_analysis_marks_location_and_reports_missing_coordinates() -> None:
    located = _listing(address="ул. Кенен Азербаев, 6", latitude=51.128, longitude=71.43)
    located_result = build_local_analysis(located, assess_listing(located, Settings()))
    assert "Местоположение подтверждено данными объявления" in located_result["strengths"]

    without_location = _listing()
    without_location_result = build_local_analysis(
        without_location,
        assess_listing(without_location, Settings()),
    )
    assert "Точный адрес и координаты не определены автоматически" in without_location_result["risks"]


def test_photo_extraction_supports_nested_gallery_objects() -> None:
    data = [
        {
            "gallery": [
                {"url": "https://cdn.example.com/one.jpg"},
                {"src": "//cdn.example.com/two.jpg"},
                {"url": "https://cdn.example.com/one.jpg"},
            ]
        }
    ]

    assert _find_image_urls(data) == [
        "https://cdn.example.com/one.jpg",
        "https://cdn.example.com/two.jpg",
    ]
