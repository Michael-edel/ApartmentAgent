"""Read Etagi's public, server-rendered apartment sale pages."""

import asyncio
import json
import math
import re
from urllib.parse import urlencode, urlsplit

from pydantic import ValidationError

from app.cities import CITY_NAMES, city_slug, is_city_coordinate_pair
from app.importer import ListingArchived, ListingImportError, ListingUnavailable
from app.market_type import MarketFilter, matches_market_type
from app.schemas import ListingCreate, plain_detail_text


_HOSTS = {'astana': 'astana.etagi.com', 'ekibastuz': 'ekibastuz.etagi.com'}
_CITIES_BY_HOST = {host: city for city, host in _HOSTS.items()}
_DETAIL_PATH = re.compile(r'/realty/(?P<id>[1-9][0-9]{0,19})/?')
_STATE_SCRIPT = re.compile(r'<script\b[^>]*>\s*var data\s*=\s*', re.IGNORECASE)
_MAX_PAGES = 3
_MAX_DETAIL_CHECKS = 8
# The public catalogue exposes these labels as flatsKeepList. On detail pages
# that list is absent even though the advert still has the same repair code.
_REPAIR_LABELS = {
    '2': 'Косметический ремонт', '3': 'Косметический ремонт — простой',
    '4': 'Косметический ремонт — свежий', '5': 'Частичный ремонт',
    '6': 'Современный ремонт', '7': 'Евроремонт', '8': 'Ремонт по дизайн-проекту',
    '9': 'Требует ремонта', '10': 'Черновая', '11': 'Улучшенная черновая отделка',
    '13': 'Предчистовая отделка', '15': 'С ремонтом', '17': 'Без стен',
    '18': 'С мебелью', '19': 'Чистовая отделка', '20': 'Под ключ',
    '21': 'Ремонт от застройщика',
}


class EtagiSearchBlocked(ListingImportError):
    """The source applied an access restriction to the request."""


def normalize_etagi_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return None
    host = parsed.netloc.lower()
    match = _DETAIL_PATH.fullmatch(parsed.path)
    if parsed.scheme not in {'http', 'https'} or host not in _CITIES_BY_HOST or match is None:
        return None
    return f'https://{host}/realty/{match["id"]}/'


def _object(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    value = str(value)
    return int(value) if re.fullmatch(r'[0-9]{1,20}', value) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object, limit: int = 500) -> str | None:
    return (plain_detail_text(value)[:limit].strip() or None) if isinstance(value, str) else None


def _state(html: str) -> dict:
    match = _STATE_SCRIPT.search(html)
    if match is not None:
        try:
            state, _ = json.JSONDecoder().raw_decode(html, match.end())
        except (ValueError, RecursionError) as exc:
            raise ListingImportError('Не удалось прочитать страницу «Этажей»') from exc
        if isinstance(state, dict):
            return state
    raise ListingImportError('Страница «Этажей» не предоставила данные объявления')


def _photos(main_photo: object, gallery: object) -> list[str]:
    urls = [main_photo]
    if isinstance(gallery, list):
        urls.extend(_object(photo).get('fname') for photo in gallery)
    accepted: list[str] = []
    for url in urls:
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if (parsed.scheme == 'https' and parsed.netloc == 'cdn.esoft.digital'
                and parsed.path.startswith('/content/') and url not in accepted):
            accepted.append(url)
            if len(accepted) == 30:
                break
    return accepted


def _listing(raw: dict, city: str, *, gallery: object = None, detail: bool = False) -> ListingCreate:
    identifier = _integer(raw.get('object_id'))
    if (not identifier or raw.get('type') != 'flat' or raw.get('class') != 'flats'
            or raw.get('isAggregatorObject') is True):
        raise ListingImportError('Страница «Этажей» не соответствует объявлению о продаже квартиры')
    status = raw.get('status')
    if status in ('archive', 'archived'):
        raise ListingArchived('Объявление «Этажей» в архиве')
    if status != 'active':
        raise ListingImportError('Не удалось подтвердить активность объявления «Этажей»')
    meta = _object(raw.get('meta'))
    if city_slug(_text(meta.get('city'))) != city:
        raise ListingImportError('Город объявления «Этажей» не совпадает с выбранным городом')
    price, area, rooms = _integer(raw.get('price')), _number(raw.get('square')), _integer(raw.get('rooms'))
    if not (price and area and area > 0 and rooms):
        raise ListingImportError('Не удалось прочитать цену, площадь или число комнат на «Этажах»')
    floor, floors = _integer(raw.get('floor')), _integer(raw.get('floors'))
    if floor and floors and floor > floors:
        floor = floors = None
    building_year = _integer(raw.get('building_year'))
    if building_year is not None and not 1900 <= building_year <= 2100:
        building_year = None
    street = _text(meta.get('street'))
    number = _text(raw.get('house_num')) or _text(raw.get('house_address_number'))
    address = ' '.join(part for part in (street, number) if part)[:500] if street else None
    latitude, longitude = _number(raw.get('la')), _number(raw.get('lo'))
    if not street or not number or not is_city_coordinate_pair(city, latitude, longitude):
        latitude = longitude = None
    repair = _REPAIR_LABELS.get(str(raw.get('keep')))
    new_building = raw.get('isNewBuilding')
    market_type = 'new' if new_building is True else 'secondary' if new_building is False else None
    extra = _object(raw.get('additional_meta'))
    bathrooms = _integer(extra.get('bathrooms_cnt'))
    if bathrooms is not None and not 1 <= bathrooms <= 20:
        bathrooms = None
    title = f'{rooms}-комнатная квартира'
    if address:
        title += f', {address}'
    return ListingCreate(
        source='etagi', source_url=f'https://{_HOSTS[city]}/realty/{identifier}/', title=title[:300],
        city=CITY_NAMES[city], city_observed=True, district=_text(meta.get('district') or meta.get('city_district')),
        address=address, latitude=latitude, longitude=longitude, market_type=market_type,
        residential_complex=_text(meta.get('newcomplex')), building_type=_text(meta.get('walls')),
        price_kzt=price, area_m2=area, rooms=rooms, floor=floor or None, floors_total=floors or None,
        building_year=building_year, bathrooms_count=bathrooms, description=_text(raw.get('notes'), 20_000),
        repair_condition=repair, repair_source_kind='field' if repair else None,
        repair_evidence=repair, seller_type='Риелтор', seller_name='Этажи',
        photo_urls=_photos(raw.get('main_photo'), gallery), details_extracted=detail, availability_status='active',
    )


def _catalogue(html: str, city: str, page: int) -> tuple[list[ListingCreate], bool]:
    state = _state(html)
    rows = _object(state.get('lists')).get('flats')
    filters = _object(_object(state.get('filters')).get('flats'))
    count, limit = _integer(filters.get('count')), _integer(filters.get('limit'))
    if not isinstance(rows, list) or count is None or not limit:
        raise ListingImportError('В каталоге «Этажей» не найдены результаты поиска квартир')
    # The public page fills the remaining places with suggested adverts even
    # when zero adverts meet the requested filters.
    remaining = max(0, count - (page - 1) * limit)
    listings: list[ListingCreate] = []
    for row in rows[:min(remaining, limit)]:
        try:
            listings.append(_listing(_object(row), city))
        except (ListingImportError, ValidationError):
            continue
    return listings, remaining > limit


async def _get(url: str) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            'curl', '--disable', '--silent', '--show-error', '--max-time', '12',
            '--max-filesize', str(4 * 1024 * 1024), '--proto', '=https',
            '--write-out', '\n%{http_code}', '--url', url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        raise ListingImportError('Не удалось запустить загрузку страницы «Этажей»') from exc
    try:
        output, _ = await process.communicate()
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    body, separator, status = output.rpartition(b'\n')
    if process.returncode != 0 or not separator or not status.isdigit() or status == b'000':
        raise ListingImportError('Не удалось загрузить страницу «Этажей»')
    code = int(status)
    if code in {403, 423, 429, 468}:
        raise EtagiSearchBlocked(f'«Этажи» ограничили доступ (HTTP {code})')
    if code in {404, 410}:
        raise ListingUnavailable(f'Страница «Этажей» недоступна (HTTP {code})')
    if code != 200:
        raise ListingImportError(f'Сайт «Этажи» вернул HTTP {code}')
    return body.decode('utf-8', errors='replace')


async def import_etagi_listing(url: str, *, expected_city: str | None = None) -> ListingCreate:
    normalized = normalize_etagi_url(url)
    if not normalized:
        raise ListingImportError('Укажите публичную ссылку на квартиру «Этажей» в Астане или Экибастузе')
    city = _CITIES_BY_HOST[urlsplit(normalized).netloc]
    if expected_city is not None and city_slug(expected_city) != city:
        raise ListingImportError('Ссылка «Этажей» относится к другому городу')
    state = _state(await _get(normalized))
    objects = _object(state.get('objects'))
    raw = _object(objects.get('flat'))
    if _integer(raw.get('object_id')) != int(_DETAIL_PATH.fullmatch(urlsplit(normalized).path)['id']):
        raise ListingImportError('Страница «Этажей» не соответствует запрошенному объявлению')
    media = _object(_object(objects.get('groupedObjectMedia')).get('byType'))
    try:
        return _listing(raw, city, gallery=media.get('photos'), detail=True)
    except ValidationError as exc:
        raise ListingImportError('В объявлении «Этажей» обнаружены некорректные характеристики') from exc


async def search_apartments(*, city: str, rooms: int, min_price_kzt: int | None = None,
                            max_price_kzt: int | None = None, min_area_m2: float | None = None,
                            max_area_m2: float | None = None, market_type: MarketFilter = 'all',
                            owner_only: bool = False, min_bathrooms_count: int | None = None) -> list[ListingCreate]:
    slug = city_slug(city)
    if slug not in _HOSTS:
        raise ListingImportError('Поиск на «Этажах» доступен для Астаны и Экибастуза')
    if owner_only:
        return []
    params = [('orderId', 'datecreatedesc')]
    params.extend(('rooms[]', str(number)) for number in range(rooms, 4))
    params.append(('rooms[]', '>4'))
    params.extend((key, f'{value:g}' if isinstance(value, float) else str(value))
                  for key, value in (('price_min', min_price_kzt), ('price_max', max_price_kzt),
                                     ('square_min', min_area_m2), ('square_max', max_area_m2))
                  if value is not None)
    if min_bathrooms_count and min_bathrooms_count >= 2:
        params.append(('bathrooms_cnt[]', '>2'))

    def fits(listing: ListingCreate) -> bool:
        return (listing.rooms >= rooms
                and (min_price_kzt is None or listing.price_kzt >= min_price_kzt)
                and (max_price_kzt is None or listing.price_kzt <= max_price_kzt)
                and (min_area_m2 is None or listing.area_m2 >= min_area_m2)
                and (max_area_m2 is None or listing.area_m2 <= max_area_m2))

    results: dict[str, ListingCreate] = {}
    seen: set[str] = set()
    checks = 0
    for page in range(1, _MAX_PAGES + 1):
        query = urlencode(params + ([('page', str(page))] if page > 1 else []))
        try:
            listings, has_next = _catalogue(await _get(f'https://{_HOSTS[slug]}/realty/?{query}'), slug, page)
        except EtagiSearchBlocked:
            raise
        except ListingImportError:
            if page == 1:
                raise
            break
        for listing in listings:
            url = str(listing.source_url)
            if url in seen or not fits(listing):
                continue
            seen.add(url)
            needs_detail = ((market_type != 'all' and listing.market_type is None)
                            or (min_bathrooms_count and listing.bathrooms_count is None))
            if needs_detail:
                if checks >= _MAX_DETAIL_CHECKS:
                    return list(results.values())
                checks += 1
                try:
                    listing = await import_etagi_listing(url, expected_city=slug)
                except EtagiSearchBlocked:
                    raise
                except ListingImportError:
                    continue
            if (fits(listing) and matches_market_type(market_type, listing.market_type)
                    and (not min_bathrooms_count or (listing.bathrooms_count or 0) >= min_bathrooms_count)):
                results[url] = listing
        if not has_next:
            break
    return list(results.values())
