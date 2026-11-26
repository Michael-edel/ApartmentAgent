import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app import etagi
from app.importer import ListingArchived, ListingImportError


def _apartment(**overrides):
    return {
        'object_id': 13703547, 'status': 'active', 'type': 'flat', 'class': 'flats',
        'price': '50000000', 'square': 108.2, 'rooms': 4, 'floor': 2, 'floors': 17,
        'la': '51.146', 'lo': '71.419', 'house_num': '7', 'building_year': 2014,
        'keep': '2', 'main_photo': 'https://cdn.esoft.digital/content/main.jpeg',
        'meta': {'city': 'Астана', 'district': 'Байконыр', 'street': 'Пример', 'walls': 'Панельные'},
        'mortgage': {'mortgageRate': 5},
        **overrides,
    }


def _html(state):
    return '<script>var data=' + json.dumps(state, ensure_ascii=False) + ';</script>'


def _catalogue(rows, *, count=None, limit=30):
    return _html({'lists': {'flats': rows}, 'filters': {'flats': {
        'count': len(rows) if count is None else count, 'limit': limit,
    }}})


def _detail(flat, photos=()):
    return _html({'objects': {'flat': flat, 'groupedObjectMedia': {
        'byType': {'photos': [{'fname': photo} for photo in photos]},
    }}})


def test_normalizes_only_supported_public_detail_urls():
    assert etagi.normalize_etagi_url('http://ASTANA.ETAGI.COM/realty/13703547?from=search') == (
        'https://astana.etagi.com/realty/13703547/'
    )
    assert etagi.normalize_etagi_url('https://ekibastuz.etagi.com/realty/14866831/')
    for url in ('https://astana.etagi.com/realty/', 'https://astana.etagi.com/realty_rent/13703547/',
                'https://astana.etagi.com.evil.invalid/realty/13703547/',
                'https://user@astana.etagi.com/realty/13703547/', 'https://almaty.etagi.com/realty/13703547/'):
        assert etagi.normalize_etagi_url(url) is None


def test_catalogue_excludes_extended_recommendations_and_foreign_cities():
    foreign = _apartment(object_id=2, meta={'city': 'Косшы'})
    suggested = _apartment(object_id=3)
    items, more = etagi._catalogue(_catalogue([_apartment(), foreign, suggested], count=2), 'astana', 1)
    assert not more and len(items) == 1
    item = items[0]
    assert (item.source, item.city, item.city_observed, item.price_kzt, item.area_m2, item.rooms) == (
        'etagi', 'Астана', True, 50000000, 108.2, 4,
    )
    assert (item.address, item.latitude, item.longitude, item.seller_type) == ('Пример 7', 51.146, 71.419, 'Риелтор')
    assert item.repair_condition == 'Косметический ремонт' and item.repair_source_kind == 'field'
    assert item.market_type is None and item.mortgage_supported is None
    assert not item.details_extracted
    assert etagi._catalogue(_catalogue([suggested], count=0), 'astana', 1) == ([], False)
    without_house = _apartment(house_num=None)
    unit = etagi._catalogue(_catalogue([without_house]), 'astana', 1)[0][0]
    assert unit.latitude is None and unit.longitude is None


@pytest.mark.asyncio
async def test_detail_keeps_gallery_and_bathrooms_checks_identity_and_city(monkeypatch):
    main = 'https://cdn.esoft.digital/content/main.jpeg'
    photo = 'https://cdn.esoft.digital/content/2.jpeg'
    published = _apartment(isNewBuilding=False, additional_meta={'bathrooms_cnt': 2, 'bathroom': [1]},
                           notes='<b>Собственный двор.</b>')

    async def get(_url):
        return _detail(published, [main, photo, photo, 'https://another-site.invalid/advert.jpeg'])

    monkeypatch.setattr(etagi, '_get', get)
    url = 'https://astana.etagi.com/realty/13703547/'
    item = await etagi.import_etagi_listing(url, expected_city='Астана')
    assert item.photo_urls == [main, photo]
    assert item.bathrooms_count == 2 and not item.bathrooms_count_is_lower_bound
    assert item.details_extracted and item.availability_status == 'active'
    assert item.market_type == 'secondary' and item.description == 'Собственный двор.'
    assert item.mortgage_supported is None and item.is_pledged is None
    with pytest.raises(ListingImportError, match='другому городу'):
        await etagi.import_etagi_listing(url, expected_city='Экибастуз')
    with pytest.raises(ListingImportError, match='запрошенному объявлению'):
        await etagi.import_etagi_listing('https://astana.etagi.com/realty/99/')
    published['status'] = 'archived'
    with pytest.raises(ListingArchived):
        await etagi.import_etagi_listing(url)


@pytest.mark.asyncio
async def test_search_uses_public_filters_and_confirms_bathrooms(monkeypatch):
    requests = []
    confirmed = _apartment(isNewBuilding=False, additional_meta={'bathrooms_cnt': 2})
    unconfirmed = _apartment(object_id=2, isNewBuilding=False, additional_meta={'bathroom': [2]})

    async def get(url):
        requests.append(url)
        parsed = urlsplit(url)
        if parsed.path == '/realty/':
            return _catalogue([_apartment(), _apartment(object_id=2), _apartment(object_id=3)], count=2)
        return _detail(confirmed if parsed.path == '/realty/13703547/' else unconfirmed)

    monkeypatch.setattr(etagi, '_get', get)
    results = await etagi.search_apartments(city='astana', rooms=2, min_price_kzt=10000000,
        max_price_kzt=50000000, min_area_m2=80, max_area_m2=120, market_type='secondary', min_bathrooms_count=2)
    assert [r.bathrooms_count for r in results] == [2]
    assert parse_qs(urlsplit(requests[0]).query) == {
        'orderId': ['datecreatedesc'], 'rooms[]': ['2', '3', '>4'], 'price_min': ['10000000'],
        'price_max': ['50000000'], 'square_min': ['80'], 'square_max': ['120'], 'bathrooms_cnt[]': ['>2'],
    }
    assert len(requests) == 3
    assert await etagi.search_apartments(city='astana', rooms=4, owner_only=True) == []
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_ekibastuz_uses_its_own_address_and_paginates_without_repeating(monkeypatch):
    requests = []
    first = _apartment(object_id=14866831, la='51.709711', lo='75.310136',
                       meta={'city': 'Экибастуз', 'street': 'Естая Беркимбаева'}, house_num='166')
    second = dict(first, object_id=14860536)

    async def get(url):
        requests.append(url)
        page = parse_qs(urlsplit(url).query).get('page', ['1'])[0]
        return _catalogue([first if page == '1' else second], count=2, limit=1)

    monkeypatch.setattr(etagi, '_get', get)
    listings = await etagi.search_apartments(city='ekibastuz', rooms=4)
    assert [str(row.source_url) for row in listings] == [
        'https://ekibastuz.etagi.com/realty/14866831/', 'https://ekibastuz.etagi.com/realty/14860536/',
    ]
    assert all(row.city == 'Экибастуз' and row.longitude == 75.310136 for row in listings)
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_http_restriction_stops_source_without_another_request(monkeypatch):
    requests = []

    class Response:
        returncode = 0

        async def communicate(self):
            return b'\n423', b''

    async def request(*args, **kwargs):
        requests.append(args[-1])
        return Response()

    monkeypatch.setattr(etagi.asyncio, 'create_subprocess_exec', request)
    with pytest.raises(etagi.EtagiSearchBlocked, match='HTTP 423'):
        await etagi.search_apartments(city='ekibastuz', rooms=4)
    assert len(requests) == 1
