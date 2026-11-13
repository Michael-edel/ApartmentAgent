"""Supported search cities and conservative result provenance."""
from typing import Literal
import math

SearchCity = Literal['astana', 'ekibastuz']
KnownCity = Literal['astana', 'ekibastuz', 'almaty', 'shymkent', 'karaganda', 'pavlodar']
SEARCH_CITIES = frozenset(('astana', 'ekibastuz'))
CITY_NAMES: dict[KnownCity, str] = {
    'astana': 'Астана', 'almaty': 'Алматы', 'shymkent': 'Шымкент',
    'karaganda': 'Караганда', 'pavlodar': 'Павлодар', 'ekibastuz': 'Экибастуз',
}
_CITY_ALIASES: dict[str, KnownCity] = {
    'астана': 'astana', 'astana': 'astana', 'нур-султан': 'astana', 'нурсултан': 'astana',
    'нұр-сұлтан': 'astana', 'нұрсұлтан': 'astana',
    'алматы': 'almaty', 'almaty': 'almaty', 'алма-ата': 'almaty',
    'шымкент': 'shymkent', 'shymkent': 'shymkent',
    'караганда': 'karaganda', 'қарағанды': 'karaganda', 'karaganda': 'karaganda', 'karagandy': 'karaganda',
    'павлодар': 'pavlodar', 'pavlodar': 'pavlodar',
    'экибастуз': 'ekibastuz', 'екібастұз': 'ekibastuz', 'ekibastuz': 'ekibastuz',
}


def city_slug(value: str | None) -> KnownCity | None:
    if not value:
        return None
    name = value.strip().casefold().split(',', 1)[0].strip()
    for prefix in ('город ', 'г. ', 'г.'):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break
    return _CITY_ALIASES.get(name)


def result_city_slug(result_city: str | None, linked_city: str | None = None) -> KnownCity | None:
    """Use a known linked card first; legacy unlabelled search results are Astana."""
    if linked_city and linked_city.strip():
        return city_slug(linked_city)
    if result_city and result_city.strip():
        return city_slug(result_city)
    return 'astana'


def city_matches(selected_city: str, result_city: str | None, linked_city: str | None = None) -> bool:
    selected = city_slug(selected_city)
    return selected is not None and result_city_slug(result_city, linked_city) == selected


# Loose sanity bounds only, not official administrative or geographic borders.
# A matched pair is still only as accurate as the original listing's coordinates.
_COORDINATE_BOUNDS: dict[KnownCity, tuple[float, float, float, float]] = {
    'astana': (50, 52, 69, 73),
    'almaty': (42.8, 43.7, 76, 77.8),
    'shymkent': (41.8, 42.7, 69, 70.2),
    'karaganda': (49.4, 50.5, 72.2, 74.3),
    'pavlodar': (51.6, 53, 75.9, 78.5),
    'ekibastuz': (51.3, 52.1, 74.8, 75.9),
}


def is_city_coordinate_pair(city: str | None, latitude: float | None, longitude: float | None) -> bool:
    """Reject incomplete, non-finite and clearly out-of-city coordinate pairs."""
    slug = city_slug(city)
    if slug is None or isinstance(latitude, bool) or isinstance(longitude, bool):
        return False
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return False
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return False
    min_lat, max_lat, min_lon, max_lon = _COORDINATE_BOUNDS[slug]
    return min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon
