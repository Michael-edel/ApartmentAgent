import json

import pytest

from app.importer import ListingImportError
from app.olx import _listing, _state, parse_olx_search_page


def _ad(**overrides):
    data = {
        'url': 'https://www.olx.kz/d/obyavlenie/example-IDabc123.html',
        'title': '4-комнатная квартира в ЖК Пример', 'category': {'id': 2982},
        'isActive': True, 'status': 'active', 'location': {'cityName': 'Астана'},
        'price': {'regularPrice': {'value': 47000000, 'currencyCode': 'KZT'}},
        'description': '2 санузла. Ипотека возможна.',
        'params': [{'key': 'obshayaploshad', 'name': 'Общая площадь', 'value': '90'},
                   {'key': 'tipsobstvennosti', 'name': 'Тип собственности', 'value': 'От хозяина'},
                   {'key': 'tip_zhilya', 'name': 'Тип жилья', 'value': 'Вторичный рынок'},
                   {'key': 'sanuzel', 'name': 'Санузел', 'value': 'Два и более'}],
        'map': {'lat': 51.1, 'lon': 71.4, 'radius': 3, 'show_detailed': False},
    }
    data.update(overrides)
    return data


def _html(state):
    return '<script>window.__PRERENDERED_STATE__= ' + json.dumps(json.dumps(state, ensure_ascii=False)) + ';</script>'


def test_public_detail_uses_only_confirmed_fields():
    item = _listing(_ad(), 'astana')
    assert (item.rooms, item.area_m2, item.price_kzt) == (4, 90, 47000000)
    assert item.seller_type == 'Хозяин недвижимости'
    assert item.residential_complex == 'Пример'
    assert (item.bathrooms_count, item.bathrooms_count_is_lower_bound) == (2, False)
    assert item.mortgage_supported is True
    assert item.latitude is None and item.longitude is None
    assert _listing(_ad(description='Два санузла.'), 'astana').bathrooms_count == 2


def test_incomplete_or_wrong_city_is_rejected():
    with pytest.raises(ListingImportError):
        _listing(_ad(location={'cityName':'Экибастуз'}), 'astana')
    with pytest.raises(ListingImportError):
        _listing(_ad(title='Квартира'), 'astana')
    with pytest.raises(ListingImportError, match='услуга по кредитованию'):
        _listing(_ad(title='Кредит вытащу с банка', description='Деньги помогу получить с банка'), 'astana')
    with pytest.raises(ListingImportError, match='услуга по кредитованию'):
        _listing(_ad(description='Продажа квартир и оформление ипотеки. Работаем с ипотекой'), 'astana')


def test_search_filters_and_safe_state_parsing():
    html = _html({'listing': {'listing': {'ads': [_ad(), _ad(url='https://example.com/d/obyavlenie/evil-IDabc123.html')]}}})
    kwargs = dict(city='astana', rooms=4, min_price_kzt=None, max_price_kzt=50000000,
                  min_area_m2=80, max_area_m2=120, market_type='secondary', owner_only=True)
    assert len(parse_olx_search_page(html, **kwargs)) == 1
    kwargs['max_price_kzt'] = 40000000
    assert not parse_olx_search_page(html, **kwargs)
    with pytest.raises(ValueError):
        _state('<title>Проверка безопасности</title>')


def test_search_url_uses_verified_price_area_and_bounded_pagination(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlsplit
    import app.olx as olx

    calls = []
    async def get(url):
        calls.append(url)
        return SimpleNamespace(text=_html({'listing': {'listing': {'totalPages': 10, 'ads': [_ad()]}}}))
    monkeypatch.setattr(olx, '_get_olx', get)
    results = asyncio.run(olx.search_olx_direct(city='astana', rooms=4, min_price_kzt=None,
        max_price_kzt=50000000, min_area_m2=80, max_area_m2=120, market_type='secondary', owner_only=True))
    assert len(results) == 1
    assert len(calls) == 3
    query = parse_qs(urlsplit(calls[0]).query)
    assert query['search[filter_float_price:to]'] == ['50000000']
    assert query['search[filter_float_obshayaploshad:from]'] == ['80']
    assert query['search[filter_float_obshayaploshad:to]'] == ['120']
    assert not any('kolichestvokomnat' in k for k in query)
