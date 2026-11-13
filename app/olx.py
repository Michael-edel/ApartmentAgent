import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from app.cities import CITY_NAMES, city_slug, is_city_coordinate_pair
from app.importer import ListingImportError, ListingUnavailable
from app.mortgage import parse_mortgage_claim
from app.schemas import ListingCreate, plain_detail_text


_OLX_ADVERT = re.compile(r"/(?:kk/)?d/obyavlenie/(?:[^/]+-)?(?P<id>ID[A-Za-z0-9]+)\.html/?")


def normalize_olx_listing_url(value: str) -> str | None:
    """Keep the public advert path and remove tracking and language variants."""
    try:
        parsed = urlsplit(value.strip())
        if (parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"olx.kz", "www.olx.kz"}
                or not _OLX_ADVERT.fullmatch(parsed.path)):
            return None
    except ValueError:
        return None
    path = parsed.path.removeprefix("/kk").rstrip("/")
    return urlunsplit(("https", "www.olx.kz", path, "", ""))


def olx_advert_id(url: str) -> str | None:
    normalized = normalize_olx_listing_url(url)
    if normalized is None:
        return None
    return _OLX_ADVERT.fullmatch(urlsplit(normalized).path).group("id")

# OLX embeds the public advert data in the server-rendered page. Only the
# requested advert or the current search page is used, never recommendation trees.
class OlxSearchBlocked(RuntimeError):
    pass


class OlxSearchError(RuntimeError):
    pass


@dataclass(slots=True)
class OlxSearchItem:
    title: str
    url: str
    snippet: str | None = None
    seller_type: str | None = None
    market_type: str | None = None
    payload: ListingCreate | None = None


_STATE_RE = re.compile(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:\\.|[^"\\])*")')
_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ApartmentAgent/1.0)',
            'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'ru-KZ,ru;q=0.9'}


def _state(html: str) -> dict:
    match = _STATE_RE.search(html)
    if not match:
        raise ValueError('OLX не предоставил данные страницы')
    try:
        result = json.loads(json.loads(match.group(1)))
    except (ValueError, TypeError) as exc:
        raise ValueError('Не удалось прочитать данные OLX') from exc
    if not isinstance(result, dict):
        raise ValueError('Неверный формат данных OLX')
    return result


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        return None
    text = str(value).strip().replace('\u00a0', '').replace(' ', '').replace(',', '.')
    if not re.fullmatch(r'\d+(?:\.\d+)?', text):
        return None
    number = float(text)
    return number if number > 0 else None


def is_olx_financing_service(title: str, description: str) -> bool:
    # Some service adverts fill the apartment category's mandatory fields.
    if re.match(r'\s*(?:кредит|ипотек[ау])\s+(?:вытащу|оформлю|помогу)', title, re.I):
        return True
    return bool(re.match(r'\s*Продажа квартир и оформление ипотеки\b', description, re.I)
                and re.search(r'работаем с ипотекой|без первоначального взноса', description, re.I))


def _approximate_map_point(ad: dict, city: str) -> tuple[float | None, float | None]:
    location = ad.get('map')
    if not isinstance(location, dict) or location.get('show_detailed') is not False:
        return None, None
    latitude, longitude = _number(location.get('lat')), _number(location.get('lon'))
    if not is_city_coordinate_pair(city, latitude, longitude):
        return None, None
    return latitude, longitude


def _listing(ad: dict, expected_city: str | None = None) -> ListingCreate:
    if not isinstance(ad, dict):
        raise ListingImportError('Нет данных объявления OLX')
    url = normalize_olx_listing_url(str(ad.get('url') or ''))
    if not url:
        raise ListingImportError('Неверная ссылка объявления OLX')
    if ad.get('isActive') is False or ad.get('status') not in (None, 'active'):
        raise ListingUnavailable('Объявление OLX недоступно')
    if (ad.get('category') or {}).get('id') != 2982:
        raise ListingImportError('Объявление не относится к продаже квартир OLX')
    location = ad.get('location') or {}
    city = str(location.get('cityName') or '').strip()
    actual_slug = city_slug(city)
    if actual_slug not in {'astana', 'ekibastuz'} or (expected_city and city_slug(expected_city) != actual_slug):
        raise ListingImportError('Город объявления OLX не подтверждён или не совпадает с выбранным')
    approximate_latitude, approximate_longitude = _approximate_map_point(ad, city)
    params = {p.get('key'): str(p.get('value') or '').strip() for p in ad.get('params', []) if isinstance(p, dict) and p.get('key')}
    title = str(ad.get('title') or '').strip()
    description = plain_detail_text(str(ad.get('description') or '')) or None
    if is_olx_financing_service(title, description or ''):
        raise ListingImportError('На OLX размещена услуга по кредитованию')
    rooms = _number(params.get('kolichestvokomnat'))
    if rooms is None:
        match = re.search(r'(?<!\d)([1-9])\s*(?:-\s*)?(?:х\s*)?(?:комнат|комн|ком\b)', title, re.I)
        rooms = int(match.group(1)) if match else None
    area = _number(params.get('obshayaploshad'))
    price_data = (ad.get('price') or {}).get('regularPrice') or {}
    price = _number(price_data.get('value')) if price_data.get('currencyCode') == 'KZT' else None
    if rooms is None or int(rooms) != rooms or area is None or price is None or not price.is_integer():
        raise ListingImportError('В объявлении OLX не подтверждены цена, площадь или количество комнат')
    market = {'Новостройки':'new', 'Вторичный рынок':'secondary'}.get(params.get('tip_zhilya'))
    seller = {'От хозяина': 'Хозяин недвижимости', 'От агентства \\ риэлтора': 'Агентство недвижимости'}.get(params.get('tipsobstvennosti'))
    bathroom = 2 if params.get('sanuzel') == 'Два и более' else None
    bathroom_lower_bound = bathroom is not None
    floor = _number(params.get('etazh'))
    floors = _number(params.get('etazhnost_doma'))
    year = _number(params.get('godpostroiki'))
    photos = [str(p) for p in ad.get('photos', []) if isinstance(p, str)][:30]
    detail = {str(p.get('name')): str(p.get('value')).strip() for p in ad.get('params', []) if isinstance(p, dict) and p.get('name') and p.get('value') and len(str(p['name'])) <= 100 and len(str(p['value'])) <= 1500}
    if description:
        counts = {int(m.group(1)) for m in re.finditer(r'(?<!\d)([1-9])\s*(?:санузл(?:а|ов)?|сан\.?\s*узл(?:а|ов)?)\b', description, re.I)}
        if re.search(r'\bдва\s+сан\.?\s*узла\b', description, re.I):
            counts.add(2)
        if len(counts) == 1 and (bathroom is None or next(iter(counts)) >= bathroom):
            bathroom, bathroom_lower_bound = next(iter(counts)), False
    mortgage = parse_mortgage_claim(description, title=title)
    mortgage_kind = ('description' if description and mortgage and mortgage[1] in description else 'title') if mortgage else None
    complex_match = re.search(r'\bЖК\s*[«"“]?([A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9 .-]{0,60})', title, re.I)
    complex_name = complex_match.group(1).strip(' .-') if complex_match else None
    try:
        return ListingCreate(source='olx-import', source_url=url, title=title, city=city, city_observed=True,
            availability_status='active',
            approximate_latitude=approximate_latitude, approximate_longitude=approximate_longitude,
            market_type=market, district=str(location.get('districtName') or '').strip() or None, residential_complex=complex_name,
            price_kzt=int(price), area_m2=area, rooms=int(rooms), description=description,
            floor=int(floor) if floor and floor.is_integer() else None,
            floors_total=int(floors) if floors and floors.is_integer() else None,
            building_year=int(year) if year and year.is_integer() else None,
            bathrooms_count=bathroom, bathrooms_count_is_lower_bound=bathroom_lower_bound,
            mortgage_supported=mortgage[0] if mortgage else None, mortgage_evidence=mortgage[1] if mortgage else None,
            mortgage_source_kind=mortgage_kind,
            building_type=params.get('tipstroeniya') or None,
            repair_condition=params.get('remont') or None,
            seller_type=seller, seller_name=(ad.get('contact') or {}).get('name') or None,
            is_pledged={'Да':True, 'Нет':False}.get(params.get('vzaloge')),
            property_details=detail, photo_urls=photos, details_extracted=bool(description and detail))
    except (ValueError, TypeError) as exc:
        raise ListingImportError('Данные объявления OLX неполные или некорректные') from exc


def parse_olx_search_page(html: str, *, city: str, rooms: int, min_price_kzt: int | None,
                          max_price_kzt: int | None, min_area_m2: float | None,
                          max_area_m2: float | None, market_type: str, owner_only: bool) -> list[OlxSearchItem]:
    state = _state(html)
    ads = state.get('listing', {}).get('listing', {}).get('ads')
    if not isinstance(ads, list):
        raise ValueError('Неверный формат выдачи OLX')
    result = {}
    for ad in ads:
        try:
            item = _listing(ad, expected_city=city)
        except ListingImportError:
            continue
        if (item.rooms < rooms or (min_price_kzt is not None and item.price_kzt < min_price_kzt)
                or (max_price_kzt is not None and item.price_kzt > max_price_kzt)
                or (min_area_m2 is not None and item.area_m2 < min_area_m2)
                or (max_area_m2 is not None and item.area_m2 > max_area_m2)
                or (market_type != 'all' and item.market_type != market_type)
                or (owner_only and item.seller_type != 'Хозяин недвижимости')):
            continue
        url = str(item.source_url)
        title = f'{item.rooms}-комнатная квартира · {item.area_m2:g} м² · {item.price_kzt} ₸ · {item.title}'
        facts = []
        if item.floor is not None and item.floors_total is not None:
            facts.append(f'{item.floor}/{item.floors_total} этаж')
        if item.building_year is not None:
            facts.append(f'год постройки {item.building_year}')
        result[url] = OlxSearchItem(title, url, ' · '.join(facts) or None, item.seller_type, item.market_type, item)
    return list(result.values())


async def _request_olx(url: str) -> httpx.Response:
    curl = shutil.which('curl')
    if curl is None:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers=_HEADERS) as client:
            return await client.get(url)
    # Use the system HTTP client on Windows and in the application image.
    # Keep redirects disabled and handle the returned HTTP status below.
    process = await asyncio.create_subprocess_exec(
        curl, '--disable', '--silent', '--show-error', '--max-time', '20',
        '--max-filesize', str(16 * 1024 * 1024), '--proto', '=https',
        '--write-out', '\n%{http_code}', '--url', url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, _ = await process.communicate()
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    body, separator, status = output.rpartition(b'\n')
    if process.returncode != 0 or not separator or not status.isdigit() or status == b'000':
        raise OlxSearchError('Не удалось подключиться к OLX')
    return httpx.Response(int(status), content=body, request=httpx.Request('GET', url))


async def _get_olx(url: str) -> httpx.Response:
    try:
        response = await _request_olx(url)
    except (OSError, httpx.HTTPError) as exc:
        raise OlxSearchError('Не удалось подключиться к OLX') from exc
    if response.status_code in {403, 429, 468}:
        raise OlxSearchBlocked(f'OLX ограничил доступ (HTTP {response.status_code})')
    if response.status_code in {404, 410}:
        raise ListingUnavailable(f'Объявление OLX недоступно (HTTP {response.status_code})')
    if response.status_code != 200:
        raise OlxSearchError(f'OLX вернул HTTP {response.status_code}')
    return response


async def search_olx_direct(*, city: str, rooms: int, min_price_kzt: int | None,
                            max_price_kzt: int | None, min_area_m2: float | None,
                            max_area_m2: float | None, market_type: str, owner_only: bool) -> list[OlxSearchItem]:
    slug = city_slug(city)
    if slug not in {'astana', 'ekibastuz'}:
        raise OlxSearchError('Поиск OLX доступен только для Астаны и Экибастуза')
    results = {}
    for page in range(1, 4):
        filters = {}
        if min_price_kzt is not None:
            filters['search[filter_float_price:from]'] = min_price_kzt
        if max_price_kzt is not None:
            filters['search[filter_float_price:to]'] = max_price_kzt
        if min_area_m2 is not None:
            filters['search[filter_float_obshayaploshad:from]'] = f'{min_area_m2:g}'
        if max_area_m2 is not None:
            filters['search[filter_float_obshayaploshad:to]'] = f'{max_area_m2:g}'
        if page > 1:
            filters['page'] = page
        url = f'https://www.olx.kz/nedvizhimost/prodazha-kvartiry/{slug}/'
        try:
            response = await _get_olx(f'{url}?{urlencode(filters)}' if filters else url)
            state = _state(response.text)
            data = state.get('listing', {}).get('listing', {})
            items = parse_olx_search_page(response.text, city=slug, rooms=rooms, min_price_kzt=min_price_kzt,
                max_price_kzt=max_price_kzt, min_area_m2=min_area_m2, max_area_m2=max_area_m2,
                market_type=market_type, owner_only=owner_only)
            for item in items:
                results[item.url] = item
            if page >= int(data.get('totalPages') or 1):
                break
        except OlxSearchBlocked:
            raise
        except (OlxSearchError, ValueError, TypeError) as exc:
            if page > 1:
                break
            raise OlxSearchError(str(exc)) from exc
    return list(results.values())


async def import_olx_listing(url: str, *, expected_city: str | None = None) -> ListingCreate:
    normalized = normalize_olx_listing_url(url)
    if not normalized:
        raise ListingImportError('Разрешены только публичные ссылки объявлений OLX.kz')
    try:
        response = await _get_olx(normalized)
        ad = _state(response.text).get('ad', {}).get('ad')
        listing = _listing(ad, expected_city)
    except (OlxSearchBlocked, OlxSearchError, ValueError) as exc:
        if isinstance(exc, ListingUnavailable):
            raise
        raise ListingImportError(str(exc)) from exc
    if olx_advert_id(str(listing.source_url)) != olx_advert_id(normalized):
        raise ListingImportError('Страница OLX не соответствует запрошенному объявлению')
    return listing
