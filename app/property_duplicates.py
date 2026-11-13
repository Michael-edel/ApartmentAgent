"""Conservative, non-destructive evidence of possible duplicate property ads."""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from app.krisha_search import normalize_krisha_listing_url
from app.seller import seller_category

AREA_DIFFERENCE_M2 = 0.5
_KRISHA_PHOTO = re.compile(
    r'^/webp/(?P<shard>[0-9a-f]{2})/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/'
    r'(?P<index>\d+)-\d{1,5}x\d{1,5}\.webp$', re.IGNORECASE,
)


def canonical_url(value: str) -> str:
    return normalize_krisha_listing_url(value) or value.strip()


def _house(value: str | None) -> str | None:
    text = (value or '').casefold().replace('ё', 'е').strip()
    # A street without a house number cannot identify a house. Apartment-unit
    # notation is not interpreted as a house number.
    if re.search(r'\b(?:кв|квартира)\.?\s*\d', text):
        return None
    if not re.search(r'(?<!\w)\d+[а-яa-z]?(?:\s*/\s*\d+[а-яa-z]?)?(?:\s*(?:корпус|корп|к)\s*\d+[а-яa-z]?)?\s*$', text):
        return None
    text = re.sub(r'[^\w]+', ' ', text).strip()
    text = re.sub(r'\b(?:улица|ул)\b', 'ул', text)
    text = re.sub(r'\b(?:проспект|просп)\b', 'проспект', text)
    return ' '.join(text.split()) or None


def _photos(row: Any) -> set[str]:
    result: set[str] = set()
    for value in getattr(row, 'photo_urls', None) or []:
        if not isinstance(value, str):
            continue
        try:
            parsed = urlsplit(value)
        except ValueError:
            continue
        path = parsed.path.casefold()
        if (parsed.scheme.casefold() not in {'http', 'https'} or not parsed.hostname
                or not path or path == '/' or path.endswith('.svg')
                or any(token in path for token in ('empty-photo', 'no-photo', 'placeholder', '/static/', 'logo'))):
            continue
        if parsed.hostname.casefold() == 'alaps-photos-kr.kcdn.kz':
            match = _KRISHA_PHOTO.fullmatch(parsed.path)
            if match and match.group('shard').casefold() == match.group('uuid')[:2].casefold():
                result.add(f"krisha:{match.group('uuid').casefold()}:{int(match.group('index'))}")
                continue
        # Query parameters may identify an image, so preserve them.
        result.add(urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path, parsed.query, '')))
    return result


def duplicate_evidence(first: Any, second: Any) -> dict[str, Any] | None:
    if canonical_url(first.source_url) == canonical_url(second.source_url):
        return {'kind': 'same_url', 'shared_photos': 0}
    first_city, second_city = (getattr(first, 'city', None) or '').strip().casefold(), (getattr(second, 'city', None) or '').strip().casefold()
    if first_city and second_city and first_city != second_city:
        return None
    first_rooms, second_rooms = getattr(first, 'rooms', None), getattr(second, 'rooms', None)
    first_area, second_area = getattr(first, 'area_m2', None), getattr(second, 'area_m2', None)
    if (first_rooms is None or second_rooms is None or first_rooms != second_rooms
            or not isinstance(first_area, (int, float)) or not isinstance(second_area, (int, float))
            or not math.isfinite(first_area) or not math.isfinite(second_area)
            or first_area <= 0 or second_area <= 0 or abs(first_area - second_area) > AREA_DIFFERENCE_M2):
        return None
    for key in ('floor', 'floors_total'):
        a, b = getattr(first, key, None), getattr(second, key, None)
        if a is not None and b is not None and a != b:
            return None
    first_bathrooms, second_bathrooms = getattr(first, 'bathrooms_count', None), getattr(second, 'bathrooms_count', None)
    if first_bathrooms is not None and second_bathrooms is not None:
        first_lower = bool(getattr(first, 'bathrooms_count_is_lower_bound', False))
        second_lower = bool(getattr(second, 'bathrooms_count_is_lower_bound', False))
        if ((not first_lower and not second_lower and first_bathrooms != second_bathrooms)
                or (first_lower and not second_lower and second_bathrooms < first_bathrooms)
                or (second_lower and not first_lower and first_bathrooms < second_bathrooms)):
            return None
    house1, house2 = _house(getattr(first, 'address', None)), _house(getattr(second, 'address', None))
    if house1 and house2 and house1 != house2:
        return None
    shared = len(_photos(first) & _photos(second))
    if shared >= 2:
        return {'kind': 'shared_photos', 'shared_photos': shared}
    first_floor, second_floor = getattr(first, 'floor', None), getattr(second, 'floor', None)
    if house1 and house1 == house2 and first_floor is not None and second_floor == first_floor:
        return {'kind': 'same_house_floor_area', 'shared_photos': shared}
    return None


def _timestamp(row: Any) -> datetime:
    value = getattr(row, 'last_imported_at', None) or getattr(row, 'updated_at', None) or getattr(row, 'created_at', None)
    if not isinstance(value, datetime):
        return datetime.min.replace(tzinfo=UTC)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def group_listings(rows: Iterable[Any], *, target: Any | None = None) -> list[list[Any]]:
    """Build cliques: every member must directly match all others, never bridge."""
    values = list(rows)
    if target is not None:
        values = [row for row in values if row.id != target.id]
    values.sort(key=lambda row: (_timestamp(row), row.id), reverse=True)
    if target is not None:
        values.insert(0, target)
    groups: list[list[Any]] = []
    for row in values:
        for group in groups:
            if all(duplicate_evidence(row, member) is not None for member in group):
                group.append(row)
                break
        else:
            groups.append([row])
    return groups


def availability(row: Any) -> str:
    # No direct-source check, only persisted status established elsewhere.
    from app.availability import listing_availability
    return listing_availability(getattr(row, 'sold_at', None), getattr(row, 'source_archived_at', None),
                                getattr(row, 'source_unavailable_at', None))


def offer(row: Any, *, target: Any | None = None) -> dict[str, Any]:
    evidence = duplicate_evidence(target, row) if target is not None and row.id != target.id else None
    return {'listing_id': row.id, 'source_url': canonical_url(row.source_url),
            'title': getattr(row, 'title', None), 'price_kzt': row.price_kzt,
            'seller_type': getattr(row, 'seller_type', None),
            'seller_category': seller_category(getattr(row, 'seller_type', None)),
            'availability': availability(row), 'is_current': bool(target is not None and row.id == target.id),
            'evidence': evidence}


def unique_offers(group: Iterable[Any], *, target: Any | None = None) -> list[dict[str, Any]]:
    """Collapse only URL spelling variants; preserve distinct ads and prices."""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    rows = list(group)
    if target is not None:
        rows.sort(key=lambda row: row.id != target.id)
    for row in rows:
        key = canonical_url(row.source_url)
        if key not in seen:
            seen.add(key)
            result.append(offer(row, target=target))
    return result
