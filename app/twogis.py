from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from math import hypot
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings


@dataclass(frozen=True, slots=True)
class TwogisResult:
    name: str
    rating: float | None
    review_count: int
    url: str

    def as_listing_fields(self) -> dict[str, object]:
        return {
            "twogis_name": self.name,
            "twogis_rating": self.rating,
            "twogis_review_count": self.review_count,
            "twogis_url": self.url,
        }


_IGNORED_NAME_WORDS = {
    "астана",
    "город",
    "жк",
    "жилой",
    "комплекс",
}


def _tokens(value: str | None) -> set[str]:
    raw = re.findall(r"[\w-]+", (value or "").casefold())
    return {token for token in raw if token not in _IGNORED_NAME_WORDS and len(token) > 1}


def _normalized_name(value: str | None) -> str:
    return " ".join(sorted(_tokens(value)))


def _number(value: object) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float, str)):
            return float(value)
        return None
    except (TypeError, ValueError):
        return None


def _point(item: dict[str, Any]) -> tuple[float, float] | None:
    point = item.get("point")
    if isinstance(point, dict):
        longitude = _number(point.get("lon", point.get("longitude")))
        latitude = _number(point.get("lat", point.get("latitude")))
    elif isinstance(point, (list, tuple)) and len(point) >= 2:
        longitude = _number(point[0])
        latitude = _number(point[1])
    else:
        return None
    if longitude is None or latitude is None:
        return None
    return latitude, longitude


def _candidate_name(item: dict[str, Any]) -> str | None:
    for key in ("name", "full_name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _review_data(item: dict[str, Any]) -> tuple[float | None, int]:
    reviews = item.get("reviews")
    if not isinstance(reviews, dict):
        return None, 0
    rating = next(
        (
            _number(reviews.get(key))
            for key in ("general_rating", "rating", "org_rating")
            if _number(reviews.get(key)) is not None
        ),
        None,
    )
    count = next(
        (
            int(number)
            for key in ("general_review_count", "review_count", "org_review_count")
            if (number := _number(reviews.get(key))) is not None
        ),
        0,
    )
    return rating, max(0, count)


def _match_score(
    query: str,
    item: dict[str, Any],
    listing_point: tuple[float, float] | None,
) -> float:
    name = _candidate_name(item) or ""
    query_normalized = _normalized_name(query)
    name_normalized = _normalized_name(name)
    if not query_normalized or not name_normalized:
        return 0
    query_tokens = _tokens(query)
    name_tokens = _tokens(name)
    overlap = len(query_tokens & name_tokens) / max(len(query_tokens), 1)
    score = overlap * 70 + SequenceMatcher(None, query_normalized, name_normalized).ratio() * 30
    if query_normalized == name_normalized:
        score += 35
    elif query_normalized in name_normalized or name_normalized in query_normalized:
        score += 20

    item_type = str(item.get("type") or item.get("subtype") or "").casefold()
    if "building" in item_type:
        score += 15

    if listing_point:
        candidate_point = _point(item)
        if candidate_point:
            distance = hypot(
                candidate_point[0] - listing_point[0], candidate_point[1] - listing_point[1]
            )
            score += max(0, 15 - distance * 100)
    return score


def parse_twogis_response(
    payload: dict[str, Any],
    residential_complex: str,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
) -> TwogisResult | None:
    result = payload.get("result")
    items = result.get("items") if isinstance(result, dict) else payload.get("items")
    if not isinstance(items, list):
        return None
    listing_point = (latitude, longitude) if latitude is not None and longitude is not None else None
    candidates = [item for item in items if isinstance(item, dict) and _candidate_name(item)]
    if not candidates:
        return None
    best = max(candidates, key=lambda item: _match_score(residential_complex, item, listing_point))
    if _match_score(residential_complex, best, listing_point) < 45:
        return None
    name = _candidate_name(best)
    if not name:
        return None
    rating, review_count = _review_data(best)
    return TwogisResult(
        name=name,
        rating=round(rating, 2) if rating is not None and 0 <= rating <= 5 else None,
        review_count=review_count,
        url=f"https://2gis.kz/astana/search/{quote(name)}",
    )


async def lookup_twogis(
    residential_complex: str | None,
    *,
    city: str,
    latitude: float | None,
    longitude: float | None,
    settings: Settings,
) -> TwogisResult | None:
    if not settings.twogis_enabled or not settings.twogis_api_key or not residential_complex:
        return None
    query = f"{residential_complex}, {city}".strip(" ,")
    params: dict[str, str | int] = {
        "q": query,
        "type": "branch,building",
        "page_size": 10,
        "locale": "ru_KZ",
        "fields": "items.point,items.reviews,items.address,items.full_address_name",
        "key": settings.twogis_api_key,
    }
    if latitude is not None and longitude is not None:
        params["location"] = f"{longitude},{latitude}"
    try:
        async with httpx.AsyncClient(timeout=settings.twogis_timeout_seconds) as client:
            response = await client.get(settings.twogis_api_url, params=params)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    return parse_twogis_response(
        body,
        residential_complex,
        latitude=latitude,
        longitude=longitude,
    )
