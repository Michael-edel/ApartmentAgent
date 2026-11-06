import httpx
import pytest

from app.importer import (
    ListingImportError,
    _address_from_text,
    _coordinates_from_html,
    _embedded_number,
    _find_coordinate,
    _find_address,
    _find_district,
    _find_numeric,
    _find_text,
    _json_scripts,
    import_krisha_listing,
)


@pytest.mark.parametrize("value", ["Астана, р-н Байконур", "Байконур р-н", "район Байконур", "Байконур район"])
def test_district_label_before_or_after_name(value):
    assert _find_district([], value) == "Байконур район"
    assert _find_district([{"regionName": value}], "") == "Байконур район"
    assert _find_district([{"districtName": value}], "") == "Байконур район"


def test_district_name_is_not_inferred_from_street_name():
    assert _find_district([], "Астана, улица Байконур 12") is None


@pytest.mark.parametrize("status_code", [403, 429, 468])
async def test_access_restrictions_suggest_manual_or_browser_import(monkeypatch, status_code: int) -> None:
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            return httpx.Response(status_code, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    with pytest.raises(ListingImportError, match=f"ограничил.*HTTP {status_code}.*браузерный импорт"):
        await import_krisha_listing("https://krisha.kz/a/show/123")


def test_reads_next_data_values() -> None:
    html = '''
    <script id="__NEXT_DATA__" type="application/json">
      {"props":{"listing":{"price":29500000,"area":56.5,"rooms":2,"floor":4,"floorsTotal":10}}}
    </script>
    '''
    data = _json_scripts(html)
    assert _find_numeric(data, {"price"}, 500_000, 2_000_000_000) == 29_500_000
    assert _find_numeric(data, {"area"}, 10, 1000) == 56.5
    assert _find_numeric(data, {"rooms"}, 1, 20) == 2


def test_reads_embedded_javascript_values() -> None:
    html = '<script>window.data={"priceKzt": "30 000 000", "areaM2": 58.2}</script>'
    assert _embedded_number(html, {"priceKzt"}, 500_000, 2_000_000_000) == 30_000_000
    assert _embedded_number(html, {"areaM2"}, 10, 1000) == 58.2


def test_reads_address_and_coordinates_from_nested_data() -> None:
    data = [
        {
            "address": {
                "streetAddress": "ул. Кенен Азербаев, 6",
                "addressLocality": "Астана",
            },
            "districtName": "Алматы р-н",
            "geo": {"latitude": "51,128", "longitude": "71.430"},
        }
    ]

    assert _find_text(data, {"address"}) == "ул. Кенен Азербаев, 6, Астана"
    assert _find_coordinate(data, {"latitude"}, -90, 90) == 51.128
    assert _find_coordinate(data, {"longitude"}, -180, 180) == 71.43


def test_employer_caption_is_skipped_before_a_structured_apartment_address():
    assert _find_address([{"seller": {"address": "Работает в компании Royal Invest"},
                           "listing": {"streetAddress": "Кудайбердыулы 36/3"}}]) == "Кудайбердыулы 36/3"
    assert _find_address([{"address": "Работает в компании Royal Invest"}]) is None


@pytest.mark.parametrize("explicit_address, expected", [(None, "Кудайбердыулы 36/3"),
                                                       ("Кенен Азербаев 6б", "Кенен Азербаев 6б")])
async def test_employer_address_does_not_override_apartment_location(monkeypatch, explicit_address, expected):
    html = '''<meta property="og:title" content="Продажа 4-комнатной квартиры №123: Кудайбердыулы 36/3, Астана">
    <script type="application/ld+json">{"price":45000000,"area":106.4,"rooms":4,
    "seller":{"address":"Работает в компании Royal Invest"}}</script>
    <div class="seller-address">Работает в компании Royal Invest</div>'''
    if explicit_address:
        html += f"<dl><dt>Адрес</dt><dd>{explicit_address}</dd></dl>"

    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result = await import_krisha_listing("https://krisha.kz/a/show/123")
    assert result.address == expected


def test_falls_back_to_visible_title_and_map_coordinates() -> None:
    text = "Продажа 2-комнатной квартиры №1014062199: Кенен Азербаев, 6б, Астана, Алматы р-н"

    assert _address_from_text(text) == "Кенен Азербаев, 6б"
    assert _coordinates_from_html('href="https://yandex.kz/maps/?ll=71.430%2C51.128&z=16"') == (
        51.128,
        71.43,
    )
    assert _coordinates_from_html('href="https://yandex.kz/maps/?ll=0.002950%2C-0.00582"') == (
        None,
        None,
    )


async def test_import_preserves_bathrooms_lower_bound(monkeypatch):
    html = '<script type="application/ld+json">{"price":45000000,"area":100,"rooms":4,"bathroomsCount":"2 с/у и более"}</script>'
    class FakeClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url):
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    result = await import_krisha_listing("https://krisha.kz/a/show/123")
    assert result.bathrooms_count == 2
    assert result.bathrooms_count_is_lower_bound is True
