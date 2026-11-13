import httpx
import pytest

from app.importer import (
    ListingImportError,
    ListingArchived,
    ListingUnavailable,
    _has_archive_notice,
    _public_details,
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


def test_archive_notice_is_visible_explicit_and_outside_description():
    banner = 'В архиве. Объявление может быть неактуальным.'
    assert _has_archive_notice(_public_details(f'<div><b>В архиве</b><p>Объявление может быть неактуальным.</p></div>')[0])
    for markup in (f'<div hidden>{banner}</div>', f'<div class="offer__description">{banner}</div>',
                   f'<div class="recommendations"><p>{banner}</p></div>',
                   '<p>Объявление продано</p>'):
        assert not _has_archive_notice(_public_details(markup)[0])


@pytest.mark.parametrize('status_code, expected', [(404, ListingUnavailable), (410, ListingUnavailable),
                                                    (403, ListingImportError), (468, ListingImportError)])
async def test_missing_page_and_access_block_are_distinct(monkeypatch, status_code, expected):
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return httpx.Response(status_code, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    with pytest.raises(expected) as caught:
        await import_krisha_listing('https://krisha.kz/a/show/123')
    assert isinstance(caught.value, ListingUnavailable) == (status_code in {404, 410})
    assert not isinstance(caught.value, ListingArchived)


@pytest.mark.asyncio
async def test_archive_precedes_populated_metadata_but_not_browser_challenge(monkeypatch):
    class FakeClient:
        html = ''
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return httpx.Response(200, text=self.html, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    banner = '<div><b>В архиве</b><p>Объявление может быть неактуальным.</p></div>'
    FakeClient.html = '<h1>3-комнатная квартира 80 м²</h1><div class="offer__price">45 000 000 ₸</div>' + banner
    with pytest.raises(ListingArchived):
        await import_krisha_listing('https://krisha.kz/a/show/123')
    FakeClient.html = '<h1>Проверка браузера</h1>' + banner
    with pytest.raises(ListingImportError) as caught:
        await import_krisha_listing('https://krisha.kz/a/show/123')
    assert not isinstance(caught.value, ListingArchived)


@pytest.mark.asyncio
async def test_missing_redirected_page_does_not_mark_original_unavailable(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            return httpx.Response(404, request=httpx.Request('GET', 'https://krisha.kz/login'))
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    with pytest.raises(ListingImportError) as caught:
        await import_krisha_listing('https://krisha.kz/a/show/123')
    assert not isinstance(caught.value, (ListingArchived, ListingUnavailable))


@pytest.mark.asyncio
async def test_positive_visible_live_offer_can_clear_stale_archive(monkeypatch):
    html = '''<h1>3-комнатная квартира 80 м²</h1><div class="offer__price">45 000 000 ₸</div>
      <dl><dt>Город</dt><dd>Астана</dd><dt>Тип дома</dt><dd>кирпичный</dd></dl>'''
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    assert (await import_krisha_listing('https://krisha.kz/a/show/123')).availability_status == 'active'


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
    <script type="application/ld+json">{"url":"https://krisha.kz/a/show/123","price":45000000,"area":106.4,"rooms":4,
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
    html = '<script type="application/ld+json">{"id":123,"price":45000000,"area":100,"rooms":4,"bathroomsCount":"2 с/у и более"}</script>'
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


@pytest.mark.asyncio
@pytest.mark.parametrize("city,latitude,longitude", [
    ("Астана", 51.128, 71.43), ("Алматы", 43.2389, 76.8897),
    ("Шымкент", 42.315, 69.59), ("Караганда", 49.806, 73.086),
    ("Павлодар", 52.287, 76.967), ("Экибастуз", 51.7237, 75.3229),
])
async def test_import_city_coordinates(monkeypatch, city, latitude, longitude):
    import json
    data = {"@type":"Apartment", "url":"https://krisha.kz/a/show/123",
            "address":{"addressLocality":city}, "geo":{"latitude":latitude,"longitude":longitude}}
    html = '<h1>3-комнатная квартира 80 м²</h1><div class="offer__price">45 000 000 ₸</div><script type="application/ld+json">' + json.dumps(data, ensure_ascii=False) + '</script>'
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    listing = await import_krisha_listing('https://krisha.kz/a/show/123')
    assert (listing.city, listing.latitude, listing.longitude) == (city, latitude, longitude)


def test_city_coordinate_checks_reject_wrong_and_nonfinite_pairs():
    from app.cities import is_city_coordinate_pair
    assert not is_city_coordinate_pair('Алматы', 51.128, 71.43)
    assert not is_city_coordinate_pair('Косшы', 51.128, 71.43)
    assert not is_city_coordinate_pair('Астана', float('nan'), 71.43)


@pytest.mark.asyncio
async def test_expected_city_used_only_when_offer_city_is_absent(monkeypatch):
    import json
    class FakeClient:
        data = {}
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            payload = {"@type":"Apartment", "url":url, **self.data}
            html = '<h1>3-комнатная квартира 80 м²</h1><div class="offer__price">45 000 000 ₸</div><script type="application/ld+json">' + json.dumps(payload, ensure_ascii=False) + '</script>'
            return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    missing = await import_krisha_listing('https://krisha.kz/a/show/123')
    assert missing.city == 'Астана' and missing.city_observed is False
    expected = await import_krisha_listing('https://krisha.kz/a/show/123', expected_city='almaty')
    assert expected.city == 'Алматы' and expected.city_observed is True
    assert 'city_observed' not in expected.model_dump()
    FakeClient.data = {'city':'Шымкент'}
    observed = await import_krisha_listing('https://krisha.kz/a/show/123', expected_city='almaty')
    assert observed.city == 'Шымкент' and observed.city_observed is True


def test_title_address_supports_other_cities():
    assert _address_from_text('Продажа квартиры №123: Абая 10, Алматы') == 'Абая 10'
    assert _address_from_text('Продажа квартиры №123: Абая 10, Экибастуз') == 'Абая 10'

@pytest.mark.asyncio
@pytest.mark.parametrize('market_type,expected', [('primary','new'), ('secondary','secondary'), ('новый дом',None), (None,None)])
async def test_explicit_market_classification_only(monkeypatch, market_type, expected):
    import json
    obj = {'@type':'Apartment','url':'https://krisha.kz/a/show/123', 'marketType':market_type,
           'buildingYear':2026, 'description':'Новый дом и свежий ремонт',
           'recommendations':[{'marketType':'secondary'}]}
    html = '<h1>3-комнатная квартира 80 м²</h1><div class="offer__price">45 000 000 ₸</div><script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False) + '</script>'
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    assert (await import_krisha_listing('https://krisha.kz/a/show/123')).market_type == expected

@pytest.mark.asyncio
@pytest.mark.parametrize('panel,market,expected', [
    ('<aside class="owners"><b>Автор объявления</b><span>Хозяин недвижимости</span></aside>', None, 'secondary'),
    ('<aside class="owners"><span>Хозяин недвижимости</span></aside>', 'primary', 'new'),
    ('<div class="recommendations"><aside class="owners"><span>Хозяин недвижимости</span></aside></div>', None, None),
    ('<div class="offer__description">Я хозяин недвижимости</div>', None, None),
])
async def test_market_owner_requires_own_visible_author_panel(monkeypatch, panel, market, expected):
    import json
    obj = {'@type':'Apartment', 'url':'https://krisha.kz/a/show/123', 'marketType':market}
    html = '<h1>3-комнатная квартира 80 м²</h1><div class="offer__price">45 000 000 ₸</div>' + panel + '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False) + '</script>'
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', FakeClient)
    assert (await import_krisha_listing('https://krisha.kz/a/show/123')).market_type == expected
