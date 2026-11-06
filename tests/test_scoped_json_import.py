"""Synthetic HTML/JSON fixtures only: these tests never access a source website."""
import json

import httpx
import pytest
from pydantic import ValidationError

from app.importer import ListingImportError, _json_scripts, _scoped_json, import_krisha_listing
from app.krisha_search import normalize_krisha_import_url
from app.schemas import ListingImportRequest


URL = "https://krisha.kz/a/show/123"


@pytest.fixture
def html_response(monkeypatch):
    fixture = {"html": ""}

    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            assert url == URL, "Only the fixture advert is requested; no network client is used"
            return httpx.Response(200, text=fixture["html"], request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return fixture


def rich_advert():
    return {
        "id": 123, "url": URL, "@type": "Apartment", "name": "Тестовая квартира",
        "offers": {"price": 45_000_000, "priceCurrency": "KZT"}, "floorSize": {"value": 106.4},
        "numberOfRooms": 4, "floorNumber": 3, "floorsTotal": 10, "yearBuilt": 2011,
        "address": {"streetAddress": "Кудайбердыулы 36/3", "addressLocality": "Астана", "addressRegion": "Алматы р-н"},
        "geo": {"latitude": 51.162, "longitude": 71.4675},
        "seller": {"@type": "Person", "id": 999, "name": "Анна", "role": "Хозяин недвижимости", "address": "Чужой адрес офиса 99"},
        "description": "Светлая квартира с удобной планировкой.\n\nКухня и спальни выходят во двор.",
        "image": [{"url": "https://images.example.test/one.jpg"}, "https://images.example.test/two.jpg"],
        "additionalProperty": [
            {"@type": "PropertyValue", "name": "Санузел", "value": "2 с/у и более"},
            {"name": "Состояние квартиры", "value": "хороший ремонт"},
            {"name": "Ипотека", "value": "Нет"}, {"name": "Балкон", "value": "лоджия"},
        ],
        "krisha_city_price_per_m2": 792_420, "krisha_district_price_per_m2": 845_700,
        "krisha_comparison_district": "Алматы р-н",
        "recommendations": [{"id": 456, "price": 1_000_000, "rooms": 1, "area": 15,
                             "photos": ["https://images.example.test/foreign.jpg"]}],
    }


@pytest.mark.parametrize("assignment", ["window.data", "var data", "let data", "const data", "window.advert", "window.__INITIAL_STATE__"])
def test_known_assignment_is_parsed_as_data_without_running_script(assignment):
    html = f'<script>{assignment} = {{"id":123,"price":45000000}}; throw new Error("must not execute");</script>'
    assert _scoped_json(_json_scripts(html), URL) == [{"id": 123, "price": 45_000_000}]


def test_comments_strings_functions_and_non_json_expressions_are_not_data():
    html = '''<script>
      // window.data = {"id":123,"price":1};
      /* window.advert = {"id":123,"price":2}; */
      const text = 'window.data = {"id":123,"price":3}';
      function dormant() { window.data = {"id":123,"price":4}; }
      window.advert = {"id":123,"price":(() => 5)()};
      window.data = fetch("https://example.test/not-requested");
    </script>'''
    assert _json_scripts(html) == []


def test_scope_matches_advert_and_never_seller_id_or_other_advert():
    data = [{"seller": {"@type": "Person", "id": 123, "name": "Not a listing"},
             "adverts": [{"id": 456, "price": 99, "area": 1}, rich_advert()],
             "relatedProducts": [{"id": 123, "price": 12345}]}]
    result = _scoped_json(data, URL)
    assert len(result) == 1 and result[0]["id"] == 123
    assert "recommendations" not in result[0]
    assert _scoped_json([{"id": 456, "url": URL, "price": 99}], URL) == []


@pytest.mark.asyncio
async def test_full_json_snapshot_imports_selected_fields_and_provenance(html_response):
    html_response["html"] = '<script>window.data = ' + json.dumps({"adverts": [
        {"id": 456, "price": 900_000, "area": 20, "rooms": 1}, rich_advert()]}, ensure_ascii=False) + ';</script>'
    value = await import_krisha_listing("123")
    assert str(value.source_url) == URL
    assert (value.price_kzt, value.area_m2, value.rooms) == (45_000_000, 106.4, 4)
    assert (value.floor, value.floors_total, value.building_year) == (3, 10, 2011)
    assert (value.address, value.city, value.district) == ("Кудайбердыулы 36/3, Астана, Алматы р-н", "Астана", "Алматы район")
    assert (value.seller_name, value.seller_type, value.repair_condition) == ("Анна", "Хозяин недвижимости", "хороший ремонт")
    assert value.mortgage_supported is False
    assert value.bathrooms_count == 2 and value.bathrooms_count_is_lower_bound
    assert value.details_extracted is True
    assert len(value.photo_urls) == 2 and all("foreign" not in photo for photo in value.photo_urls)
    assert value.description == rich_advert()["description"]
    assert "\n\n" in value.description
    assert (value.latitude, value.longitude) == (51.162, 71.4675)
    assert value.property_details["Балкон"] == "лоджия"
    assert (value.krisha_city_price_per_m2, value.krisha_district_price_per_m2) == (792_420, 845_700)


@pytest.mark.asyncio
async def test_dom_explicit_values_override_json_without_erasing_other_json_details(html_response):
    html_response["html"] = '<script type="application/ld+json">' + json.dumps(rich_advert(), ensure_ascii=False) + '''</script>
    <div class="offer__price">44 000 000 ₸</div><dl><dt>Площадь</dt><dd>104 м²</dd>
    <dt>Количество комнат</dt><dd>3</dd><dt>Санузел</dt><dd>раздельный</dd>
    <dt>Ремонт</dt><dd>требуется ремонт</dd><dt>Ипотека</dt><dd>Да</dd>
    <dt>Адрес</dt><dd>Другой адрес 12</dd></dl>'''
    value = await import_krisha_listing(URL)
    assert (value.price_kzt, value.area_m2, value.rooms) == (44_000_000, 104, 3)
    assert value.bathrooms_count == 1 and not value.bathrooms_count_is_lower_bound
    assert value.repair_condition == "требуется ремонт" and value.mortgage_supported is True
    assert value.address == "Другой адрес 12" and value.property_details["Балкон"] == "лоджия"


@pytest.mark.asyncio
async def test_visible_headline_and_labelled_price_override_cached_json(html_response):
    html_response["html"] = '<script type="application/json">' + json.dumps(rich_advert()) + '''</script>
    <h1>3-комнатная квартира 98.2 м², 5/9 этаж, Кудайбердыулы 36/3</h1>
    <div class="offer__info-item"><span>Цена</span><span>44 000 000 ₸</span></div>'''
    value = await import_krisha_listing(URL)
    assert (value.price_kzt, value.area_m2, value.rooms, value.floor, value.floors_total) == (44_000_000, 98.2, 3, 5, 9)


@pytest.mark.asyncio
@pytest.mark.parametrize("headline_address", ["Айтматова 21", "Е-321 17/3, 17/2, 17/1, 15/1, 15/2, 15/3"])
async def test_own_headline_house_address_precedes_json_office_address(html_response, headline_address):
    advert = rich_advert()
    advert["address"] = {"streetAddress": "пр. Улы Дала, 13", "addressLocality": "Астана", "addressRegion": "Нура р-н"}
    html_response["html"] = '<script type="application/json">' + json.dumps(advert) + '</script>' + (
        f'<h1>4-комнатная квартира · 95.66 м², {headline_address}</h1>')
    value = await import_krisha_listing(URL)
    assert value.address == headline_address
    html_response["html"] += '<dl><dt>Адрес</dt><dd>Явный адрес 8</dd></dl>'
    assert (await import_krisha_listing(URL)).address == "Явный адрес 8"


@pytest.mark.asyncio
async def test_empty_photo_placeholders_are_not_apartment_photos(html_response):
    advert = rich_advert()
    advert["image"].append("https://krisha.kz/static/frontend/images/empty-photo-flat.png")
    html_response["html"] = '<meta property="og:image" content="https://krisha.kz/static/frontend/images/empty-photo-flat.svg">' + (
        '<script type="application/json">' + json.dumps(advert) + '</script>')
    value = await import_krisha_listing(URL)
    assert value.photo_urls == ["https://images.example.test/one.jpg", "https://images.example.test/two.jpg"]


@pytest.mark.asyncio
async def test_basic_matched_json_does_not_claim_full_details(html_response):
    html_response["html"] = '<script type="application/json">{"id":123,"price":45000000,"rooms":4,"area":100}</script>'
    value = await import_krisha_listing(URL)
    assert not value.details_extracted and not value.property_details


@pytest.mark.asyncio
async def test_visible_complete_coordinate_pair_overrides_json_and_never_mix_sources(html_response):
    for geo in ({"latitude": 51.162, "longitude": 71.4675}, {"latitude": 51.162}):
        advert = rich_advert()
        advert["geo"] = geo
        html_response["html"] = '<script type="application/ld+json">' + json.dumps(advert) + '</script>' + (
            '<a href="https://yandex.kz/maps/?ll=71.430%2C51.128">Карта квартиры</a>')
        value = await import_krisha_listing(URL)
        assert (value.latitude, value.longitude) == (51.128, 71.43)


@pytest.mark.asyncio
async def test_two_partial_json_coordinate_objects_cannot_form_one_location(html_response):
    first = rich_advert()
    first["geo"] = {"latitude": 51.162}
    second = {"id": 123, "price": 45_000_000, "longitude": 71.4675}
    html_response["html"] = '<script type="application/ld+json">' + json.dumps([first, second]) + '</script>'
    value = await import_krisha_listing(URL)
    assert value.latitude is None and value.longitude is None


@pytest.mark.asyncio
async def test_matched_webpage_can_identify_its_explicit_apartment_main_entity(html_response):
    entity = rich_advert()
    entity.pop("id")
    entity.pop("url")
    html_response["html"] = '<script type="application/ld+json">' + json.dumps({
        "@type": "WebPage", "url": URL, "mainEntity": entity,
    }, ensure_ascii=False) + '</script>'
    value = await import_krisha_listing(URL)
    assert value.price_kzt == 45_000_000 and value.area_m2 == 106.4
    assert value.seller_name == "Анна" and value.details_extracted
    entity["id"] = 456
    html_response["html"] = '<script type="application/ld+json">' + json.dumps({
        "@type": "WebPage", "url": URL, "mainEntity": entity,
    }, ensure_ascii=False) + '</script>'
    with pytest.raises(ListingImportError, match="Не удалось определить"):
        await import_krisha_listing(URL)


@pytest.mark.asyncio
async def test_unrelated_or_unidentified_json_cannot_supply_required_numbers(html_response):
    for data in ({"id": 456, "price": 45_000_000, "rooms": 4, "area": 100},
                 {"price": 45_000_000, "rooms": 4, "area": 100},
                 {"seller": {"id": 123, "@type": "Person"}, "related": [rich_advert()]}):
        html_response["html"] = '<script>window.data=' + json.dumps(data) + '</script>'
        with pytest.raises(ListingImportError, match="Не удалось определить"):
            await import_krisha_listing(URL)


@pytest.mark.asyncio
async def test_partial_json_never_borrows_price_address_or_district_from_body_recommendations(html_response):
    html_response["html"] = '''<script type="application/json">{"id":123,"area":96,"rooms":4}</script>
    <h1>4-комнатная квартира 96 м²</h1>
    <section class="recommendations">Цена 99 000 000 ₸, ул. Чужая 99, Есильский р-н</section>'''
    with pytest.raises(ListingImportError, match="цену"):
        await import_krisha_listing(URL)
    html_response["html"] += '<div class="offer__price">38 000 000 ₸</div>'
    value = await import_krisha_listing(URL)
    assert (value.price_kzt, value.area_m2, value.rooms) == (38_000_000, 96, 4)
    assert value.address is None and value.district is None


@pytest.mark.asyncio
async def test_bad_json_still_uses_visible_card_and_recaptcha_footer_is_not_a_challenge(html_response):
    html_response["html"] = '''<title>4-комнатная квартира</title><h1>4-комнатная квартира 100 м²</h1>
    <div class="offer__price">45 000 000 ₸</div><script>window.data={malformed};</script>
    <div class="grecaptcha-badge">Сайт защищён reCAPTCHA</div>'''
    value = await import_krisha_listing(URL)
    assert (value.price_kzt, value.area_m2, value.rooms) == (45_000_000, 100, 4)


@pytest.mark.parametrize("value", ["123", " 1010095181 ", "https://www.krisha.kz/a/show/123/?from=card"])
def test_number_or_url_import_contract(value):
    expected = "https://krisha.kz/a/show/1010095181" if "1010095181" in value else URL
    assert normalize_krisha_import_url(value) == expected
    assert str(ListingImportRequest(source_url=value).source_url) == expected


@pytest.mark.parametrize("value", ["0", "-123", "1.5", "000123", "1e9", "https://example.test/a/show/123", "https://krisha.kz/a/show/123/extra", "https://u:p@krisha.kz/a/show/123", "https://krisha.kz:8000/a/show/123", 123, True])
def test_invalid_identifier_is_rejected(value):
    assert normalize_krisha_import_url(value) is None
    with pytest.raises(ValidationError):
        ListingImportRequest(source_url=value)
