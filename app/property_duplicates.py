"""Conservative, non-destructive evidence of possible duplicate property ads."""
from __future__ import annotations

import math
import re
from difflib import SequenceMatcher
from datetime import UTC, datetime
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select

from app.cities import city_slug
from app.krisha_search import normalize_krisha_listing_url
from app.models import DuplicateDecision
from app.olx import normalize_olx_listing_url, olx_advert_id
from app.seller import seller_category

AREA_DIFFERENCE_M2 = 0.5
Decisions = dict[tuple[int, int], str]
_KRISHA_PHOTO = re.compile(
    r'^/webp/(?P<shard>[0-9a-f]{2})/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/'
    r'(?P<index>\d+)-\d{1,5}x\d{1,5}\.webp$', re.IGNORECASE,
)


def canonical_url(value: str) -> str:
    return normalize_krisha_listing_url(value) or normalize_olx_listing_url(value) or value.strip()


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


def first_floor_present(first: Any, second: Any) -> bool:
    a, b = getattr(first, 'floor', None), getattr(second, 'floor', None)
    return isinstance(a, int) and not isinstance(a, bool) and a > 0 and a == b


def _shared_description(first: Any, second: Any) -> bool:
    """Require long, nearly identical seller text across the two official sites."""
    def tokens(value: Any) -> list[str]:
        if not isinstance(value, str):
            return []
        # Limit work on untrusted text while retaining the offer's description.
        return re.findall(r"[^\W_]+", value[:12000].casefold().replace('ё', 'е'), re.UNICODE)[:1200]

    left, right = tokens(getattr(first, 'description', None)), tokens(getattr(second, 'description', None))
    if min(len(left), len(right)) < 60:
        return False
    # Matching contiguous wording and high overlap resist ordinary boilerplate.
    matches = SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks()
    overlap = sum(match.size for match in matches)
    longest = max(match.size for match in matches)
    return longest >= 35 and overlap >= 60 and overlap / min(len(left), len(right)) >= 0.8


def decision_key(first_id: int, second_id: int) -> tuple[int, int]:
    return min(first_id, second_id), max(first_id, second_id)


async def load_duplicate_decisions(session) -> Decisions:
    rows = await session.execute(select(DuplicateDecision.first_id, DuplicateDecision.second_id, DuplicateDecision.decision))
    return {(a, b): decision for a, b, decision in rows}


def confirmed_component(listing_id: int, decisions: Decisions) -> set[int]:
    neighbours: dict[int, set[int]] = {}
    for (left, right), decision in decisions.items():
        if decision == 'same':
            neighbours.setdefault(left, set()).add(right)
            neighbours.setdefault(right, set()).add(left)
    found = {listing_id}
    pending = [listing_id]
    while pending:
        new = neighbours.get(pending.pop(), set()) - found
        found.update(new)
        pending.extend(new)
    return found


def duplicate_evidence(first: Any, second: Any, *, decisions: Decisions | None = None) -> dict[str, Any] | None:
    if canonical_url(first.source_url) == canonical_url(second.source_url):
        return {'kind': 'same_url', 'shared_photos': 0}
    first_olx, second_olx = olx_advert_id(first.source_url), olx_advert_id(second.source_url)
    if first_olx and first_olx == second_olx:
        return {'kind': 'same_url', 'shared_photos': 0}
    if decisions:
        decision = decisions.get(decision_key(first.id, second.id))
        if decision == 'same':
            return {'kind': 'confirmed', 'shared_photos': 0}
        if decision == 'different':
            return None
    first_city, second_city = (getattr(first, 'city', None) or '').strip().casefold(), (getattr(second, 'city', None) or '').strip().casefold()
    if (first_city and second_city
            and (city_slug(first_city) or first_city) != (city_slug(second_city) or second_city)):
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
    if bool(first_olx) != bool(second_olx) and house1 and house2:
        # The two sites format street prefixes differently. Preserve house and
        # corpus numbers, and keep contradictory explicit street types apart.
        kind1, _, name1 = house1.partition(' ')
        kind2, _, name2 = house2.partition(' ')
        street_kinds = {'ул', 'проспект'}
        if kind1 in street_kinds and kind2 not in street_kinds:
            house1 = name1
        if kind2 in street_kinds and kind1 not in street_kinds:
            house2 = name2
    if house1 and house2 and house1 != house2:
        return None
    first_krisha = bool(normalize_krisha_listing_url(first.source_url))
    second_krisha = bool(normalize_krisha_listing_url(second.source_url))
    cross_platform = (first_krisha and bool(second_olx)) or (second_krisha and bool(first_olx))
    if (cross_platform and first_city and second_city and first_floor_present(first, second)
            and _shared_description(first, second)):
        return {'kind': 'shared_description', 'shared_photos': 0}
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


def group_listings(rows: Iterable[Any], *, target: Any | None = None, decisions: Decisions | None = None) -> list[list[Any]]:
    """Group confirmed links first. Automatic matches must agree with every member."""
    values = list(rows)
    if target is not None:
        values = [row for row in values if row.id != target.id]
    values.sort(key=lambda row: (_timestamp(row), row.id), reverse=True)
    if target is not None:
        values.insert(0, target)
    groups: list[list[Any]] = []
    included: set[int] = set()
    for row in values:
        if row.id in included:
            continue
        linked = confirmed_component(row.id, decisions) if decisions else {row.id}
        cluster = [member for member in values if member.id in linked] if len(linked) > 1 else [row]
        included.update(member.id for member in cluster)
        for group in groups:
            if all(duplicate_evidence(candidate, member, decisions=decisions) is not None
                   for candidate in cluster for member in group):
                group.extend(cluster)
                break
        else:
            groups.append(cluster)
    return groups


def cross_platform_listing_ids(rows: Iterable[Any], decisions: Decisions) -> set[int]:
    listings = list(rows)
    olx = {row.id for row in listings if olx_advert_id(row.source_url)}
    krisha = {row.id for row in listings if normalize_krisha_listing_url(row.source_url)}
    if not olx or not krisha:
        return set()
    result = set()
    for group in group_listings(listings, decisions=decisions):
        ids = {row.id for row in group}
        if ids & olx and ids & krisha:
            result.update(ids & (olx | krisha))
    return result


def duplicate_hidden_ids(rows: Iterable[Any], hidden_urls: set[str], decisions: Decisions) -> set[int]:
    if not hidden_urls:
        return set()
    listings = list(rows)
    hidden = [row for row in listings if canonical_url(row.source_url) in hidden_urls]
    confirmed = {listing_id for row in hidden for listing_id in confirmed_component(row.id, decisions)}
    return {row.id for row in listings if canonical_url(row.source_url) not in hidden_urls
            and (row.id in confirmed or any(duplicate_evidence(row, other, decisions=decisions) for other in hidden))}


def availability(row: Any) -> str:
    # No direct-source check, only persisted status established elsewhere.
    from app.availability import listing_availability
    return listing_availability(getattr(row, 'sold_at', None), getattr(row, 'source_archived_at', None),
                                getattr(row, 'source_unavailable_at', None))


def offer(row: Any, *, target: Any | None = None, decisions: Decisions | None = None) -> dict[str, Any]:
    evidence = None
    if target is not None and row.id != target.id:
        if decisions and row.id in confirmed_component(target.id, decisions):
            evidence = {'kind': 'confirmed', 'shared_photos': 0}
        else:
            evidence = duplicate_evidence(target, row, decisions=decisions)
    photos = getattr(row, 'photo_urls', None) or []
    return {'listing_id': row.id, 'source_url': canonical_url(row.source_url),
            'title': getattr(row, 'title', None), 'price_kzt': row.price_kzt,
            'photo_url': next(iter(photos), None), 'area_m2': getattr(row, 'area_m2', None),
            'seller_type': getattr(row, 'seller_type', None),
            'seller_category': seller_category(getattr(row, 'seller_type', None)),
            'availability': availability(row), 'is_current': bool(target is not None and row.id == target.id),
            'evidence': evidence}


def unique_offers(group: Iterable[Any], *, target: Any | None = None, decisions: Decisions | None = None) -> list[dict[str, Any]]:
    """Collapse only URL spelling variants; preserve distinct ads and prices."""
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    rows = list(group)
    if target is not None:
        rows.sort(key=lambda row: row.id != target.id)
    for row in rows:
        olx_id = olx_advert_id(row.source_url)
        key = ("olx", olx_id) if olx_id else ("url", canonical_url(row.source_url))
        if key not in seen:
            seen.add(key)
            result.append(offer(row, target=target, decisions=decisions))
    return result
