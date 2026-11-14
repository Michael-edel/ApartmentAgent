"""Conservative, side-effect-free search filters for confirmed listing facts.

Unknown facts are returned separately. A drawn area always requires an exact
location. For other conditions, ``require_confirmed`` controls unknown facts.
Travel-time calculations are intentionally outside this module.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

from app.market_comparison import _district
from app.seller import seller_category


def _text(value: object) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _normalized(value: object) -> str:
    return _text(value).casefold().replace('ё', 'е')


def _point(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    a, b = value
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a, b)):
        return None
    if not -90 <= a <= 90 or not -180 <= b <= 180:
        return None
    return float(a), float(b)


def point_in_polygon(latitude: float, longitude: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Use [latitude, longitude] vertices; edges count as inside."""
    point = _point((latitude, longitude))
    if point is None or not isinstance(polygon, (tuple, list)) or not 3 <= len(polygon) <= 200:
        raise ValueError('Некорректный полигон')
    vertices = [_point(vertex) for vertex in polygon]
    if any(vertex is None for vertex in vertices):
        raise ValueError('Некорректные координаты полигона')
    x, y = point[1], point[0]
    inside = False
    for (ay, ax), (by, bx) in zip(vertices, vertices[1:] + vertices[:1]):
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if abs(cross) <= 1e-10 and min(ax, bx) - 1e-10 <= x <= max(ax, bx) + 1e-10 and min(ay, by) - 1e-10 <= y <= max(ay, by) + 1e-10:
            return True
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


_LAYOUT_PATTERNS = {
    'separate': re.compile(r'\b(?:раздельн\w*|изолированн\w*)\s+(?:комнат\w*|планировк\w*)\b|\bпланировк\w*\s*[:—-]?\s*(?:раздельн\w*|изолированн\w*)', re.I),
    'adjoining': re.compile(r'\b(?:смежн\w*|проходн\w*)\s+комнат\w*\b|\bпланировк\w*\s*[:—-]?\s*смежн\w*', re.I),
    'studio': re.compile(r'\b(?:квартира\s*[-—]?\s*студия|планировк\w*\s*[:—-]?\s*студия)\b', re.I),
    'cross_ventilated': re.compile(r'\b(?:квартира\s*[-—]?\s*распашонка|окна\s+на\s+(?:две|2)\s+стороны)\b', re.I),
}
_FIELD_LAYOUTS = {'раздельная':'separate', 'изолированная':'separate', 'смежная':'adjoining',
                  'студия':'studio', 'распашонка':'cross_ventilated'}
_NEGATION = re.compile(r'\b(?:не|нет|без|кроме|возможно|возможно ли|ли|соседн\w*|друг\w*)\b', re.I)


def layout_evidence(item: Mapping) -> dict | None:
    details = item.get('property_details')
    if isinstance(details, Mapping):
        for key, value in details.items():
            if _normalized(key).rstrip(':') == 'планировка':
                layout = _FIELD_LAYOUTS.get(_normalized(value).rstrip('.'))
                if layout:
                    return {'value': layout, 'source': 'field', 'confidence': 'explicit', 'evidence': _text(value)}
    for source in ('title', 'description'):
        text = _text(item.get(source))[:20000]
        claims = []
        for unit in re.split(r'(?<=[.!?;])\s+', text):
            if '?' in unit or _NEGATION.search(unit):
                continue
            for layout, pattern in _LAYOUT_PATTERNS.items():
                match = pattern.search(unit)
                if match:
                    claims.append((layout, match.group()))
        values = {value for value, _ in claims}
        if len(values) == 1:
            value, evidence = claims[0]
            return {'value': value, 'source': source, 'confidence': 'text', 'evidence': evidence}
        if len(values) > 1:
            return None
    return None


def phrase_evidence(item: Mapping, phrase: str) -> dict | None:
    """Literal case-insensitive match with source, never semantic inference."""
    needle = _normalized(phrase)
    if not needle or len(needle) > 200:
        return None
    for source in ('title', 'description'):
        text = _text(item.get(source))[:20000]
        for unit in re.split(r'(?<=[.!?;])\s+', text):
            if needle in _normalized(unit) and '?' not in unit and (
                _NEGATION.search(needle) or not _NEGATION.search(unit)
            ):
                return {'source': source, 'confidence': 'literal', 'evidence': unit[:500]}
    return None


_FEATURE_PATTERNS = {
    'kitchen_living': re.compile(r'\bкухн\w*\s*[-–—]\s*(?:гостин\w*|студи\w*)\b|\b(?:гостиная\s+совмещена\s+с\s+кухней|кухня\s+совмещена\s+с\s+гостиной)\b', re.I),
    'closet': re.compile(r'\bгардеробн\w*\b', re.I),
    'balcony': re.compile(r'\b(?:балкон\w*|лоджи\w*)\b', re.I),
}


def feature_evidence(item: Mapping, feature: str) -> dict | None:
    pattern = _FEATURE_PATTERNS.get(feature)
    if pattern is None:
        return None
    details = item.get('property_details')
    if isinstance(details, Mapping):
        for key, value in details.items():
            label, content = _text(key), _text(value)
            if pattern.search(label) and _normalized(content) in {'нет', 'не имеется', 'отсутствует'}:
                return {'value': False, 'source': 'field', 'confidence': 'explicit', 'evidence': f'{label}: {content}'[:500]}
            if pattern.search(label) and _normalized(content) in {'да', 'есть', 'имеется'}:
                return {'value': True, 'source': 'field', 'confidence': 'explicit', 'evidence': f'{label}: {content}'[:500]}
    claims = []
    for source in ('title', 'description'):
        text = _text(item.get(source))[:20000]
        for unit in re.split(r'(?<=[.!?;])\s+', text):
            match = pattern.search(unit)
            if not match or '?' in unit or re.search(r'\b(?:соседн\w*|друг\w*|возможно|планируется)\b', unit, re.I):
                continue
            before, after = unit[max(0, match.start()-30):match.start()], unit[match.end():match.end()+20]
            negative = bool(re.search(r'\b(?:нет|без|не имеется)\s*$', before, re.I) or re.match(r'\s*(?:нет|отсутствует)\b', after, re.I))
            if not negative and _NEGATION.search(unit):
                continue
            claims.append({'value': not negative, 'source':source, 'confidence':'text', 'evidence':unit[:500]})
    if len({entry['value'] for entry in claims}) == 1:
        return claims[0]
    return None


def distance_m(first: Sequence[float], second: Sequence[float]) -> float:
    """Great-circle distance in metres; not walking or driving time."""
    a, b = _point(first), _point(second)
    if a is None or b is None:
        raise ValueError('Некорректные координаты')
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2 * 6371008.8 * math.asin(math.sqrt(min(1.0, max(0.0, h))))


def is_confirmed_realtor_duplicate(candidate: Mapping, other: Mapping, *, same_apartment_confirmed: bool) -> bool:
    """Require same verified apartment, same source and explicit seller ID."""
    if not same_apartment_confirmed or seller_category(candidate.get('seller_type')) != 'realtor':
        return False
    first_id, second_id = candidate.get('seller_id'), other.get('seller_id')
    if not isinstance(first_id, (str, int)) or isinstance(first_id, bool) or not str(first_id).strip():
        return False
    if str(first_id) != str(second_id):
        return False
    def host(value):
        try:
            parsed = urlsplit(str(value or ''))
            return (parsed.hostname or '').lower().removeprefix('www.') if parsed.scheme in ('https', 'http') else ''
        except ValueError:
            return ''
    left, right = host(candidate.get('source_url')), host(other.get('source_url'))
    return bool(left and left == right and left in {'krisha.kz', 'olx.kz'})


def evaluate_search_quality(item: Mapping, criteria: Mapping) -> dict:
    """Evaluate optional filters. Reasons carry evidence; unknown is explicit."""
    reasons, unknown = [], []
    def record(kind, passed, evidence=None):
        reasons.append({'criterion': kind, 'passed': passed, 'evidence': evidence})
    districts = criteria.get('districts') or []
    if districts:
        district = _district(_text(item.get('district')))
        if not district: unknown.append('district')
        else: record('district', district in {_district(d) for d in districts}, {'source':'district', 'value':_text(item.get('district'))})
    polygon = criteria.get('polygon')
    if polygon is not None:
        # Validate polygon regardless of whether the listing has a location.
        point_in_polygon(0, 0, polygon)
        point = None if item.get('approximate_location') is True else _point((item.get('latitude'), item.get('longitude')))
        if point is None: unknown.append('polygon')
        else: record('polygon', point_in_polygon(*point, polygon), {'source':'exact_coordinates'})
    radius = criteria.get('radius')
    if radius is not None:
        if not isinstance(radius, Mapping):
            raise ValueError('Некорректный радиус')
        origin = _point((radius.get('latitude'), radius.get('longitude')))
        maximum = radius.get('max_m')
        if origin is None or isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or not math.isfinite(maximum) or not 0 < maximum <= 100000:
            raise ValueError('Некорректный радиус')
        point = None if item.get('approximate_location') is True else _point((item.get('latitude'), item.get('longitude')))
        if point is None: unknown.append('radius')
        else:
            metres = distance_m(origin, point)
            record('radius', metres <= maximum, {'source':'exact_coordinates', 'distance_m':round(metres), 'max_m':maximum})
    for feature in criteria.get('features') or []:
        evidence = feature_evidence(item, feature)
        if evidence is None: unknown.append('feature:' + str(feature))
        else: record('feature:' + str(feature), evidence['value'], evidence)
    layout = criteria.get('layout')
    if layout and layout != 'all':
        evidence = layout_evidence(item)
        if evidence is None: unknown.append('layout')
        else: record('layout', evidence['value'] == layout, evidence)
    phrase = _text(criteria.get('phrase'))
    if phrase:
        evidence = phrase_evidence(item, phrase)
        if evidence: record('phrase', True, evidence)
        elif not _text(item.get('description')): unknown.append('phrase')
        else: record('phrase', False)
    if criteria.get('exclude_pledged'):
        pledged = item.get('is_pledged')
        if pledged is None: unknown.append('pledge')
        elif isinstance(pledged, bool): record('pledge', not pledged, {'source':'field', 'value':pledged})
        else: unknown.append('pledge')
    if criteria.get('exclude_lower_bound'):
        lower = item.get('price_is_lower_bound')
        if lower is None: unknown.append('lower_bound')
        elif isinstance(lower, bool): record('lower_bound', not lower, {'source':'field', 'value':lower})
        else: unknown.append('lower_bound')
    if criteria.get('exclude_cash_only'):
        cash = item.get('cash_only')
        if isinstance(cash, bool): record('cash_only', not cash, {'source':'field', 'value':cash})
        else: unknown.append('cash_only')
    matches = (all(reason['passed'] for reason in reasons) and 'polygon' not in unknown
               and not (criteria.get('require_confirmed') and unknown))
    return {'matches': matches, 'reasons': reasons, 'unknown': unknown}
