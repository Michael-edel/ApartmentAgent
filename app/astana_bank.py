"""Determine the bank of the Esil from an Astana map pin."""

from itertools import pairwise
from typing import Literal

from app._esil_geometry import ESIL_CENTERLINE, ESIL_SIDE_CHANNEL


AstanaBank = Literal["left", "right", "unknown"]

# Use only the urban reach covered continuously by the river extract.
_SOUTH, _NORTH = 51.03, 51.22
_WEST, _EAST = 71.328, 71.59
_METRES_PER_LATITUDE = 111_200
_METRES_PER_LONGITUDE = 69_700
_UNCERTAIN_METRES = 250
_CENTER_SEGMENTS = tuple(pairwise(ESIL_CENTERLINE))
_RIVER_SEGMENTS = _CENTER_SEGMENTS + tuple(pairwise(ESIL_SIDE_CHANNEL))


def _near_river(latitude: float, longitude: float) -> bool:
    for (start_lat, start_lon), (end_lat, end_lon) in _RIVER_SEGMENTS:
        dx = (end_lon - start_lon) * _METRES_PER_LONGITUDE
        dy = (end_lat - start_lat) * _METRES_PER_LATITUDE
        x = (longitude - start_lon) * _METRES_PER_LONGITUDE
        y = (latitude - start_lat) * _METRES_PER_LATITUDE
        fraction = max(0.0, min(1.0, (x * dx + y * dy) / (dx * dx + dy * dy)))
        if (x - fraction * dx) ** 2 + (y - fraction * dy) ** 2 <= _UNCERTAIN_METRES ** 2:
            return True
    return False


def astana_bank(latitude: float | None, longitude: float | None) -> AstanaBank:
    if latitude is None or longitude is None:
        return "unknown"
    if not (_SOUTH <= latitude <= _NORTH and _WEST <= longitude <= _EAST):
        return "unknown"
    if _near_river(latitude, longitude):
        return "unknown"

    # Esil flows east to west through Astana. South of the whole centerline
    # is the left bank. Crossing the river along a ray from the south toggles
    # the side, which also handles stretches with several bends at one longitude.
    right_bank = False
    for (start_lat, start_lon), (end_lat, end_lon) in _CENTER_SEGMENTS:
        if (start_lon > longitude) != (end_lon > longitude):
            crossing = start_lat + (end_lat - start_lat) * (longitude - start_lon) / (end_lon - start_lon)
            if latitude > crossing:
                right_bank = not right_bank
    return "right" if right_bank else "left"
