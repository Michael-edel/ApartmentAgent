"""Comparison with the user's recently imported listings, not a market index."""

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from app.cities import CITY_NAMES, city_slug, is_city_coordinate_pair
from app.krisha_search import normalize_krisha_listing_url
from app.property_duplicates import availability, group_listings, unique_offers, _timestamp
from app.seller import seller_category

LOOKBACK_DAYS = 90
MIN_SAMPLES = 5
COMPARABLE_RADIUS_M = 1000
AREA_TOLERANCE_PERCENT = 20


def _district(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    text = value.casefold().replace("ё", "е").replace("й", "и")
    text = re.sub(r"\b(?:раиона|раион|р-н|р\s*н)\b", " ", text)
    text = re.sub(r"[^\w]+", " ", text).strip()
    aliases = {
        "есильскии": "есиль", "есиль": "есиль", "есил": "есиль", "есіл": "есиль",
        "нуринскии": "нура", "нура": "нура",
        "алматинскии": "алматы", "алматы": "алматы",
        "сарыаркинскии": "сарыарка", "сарыарка": "сарыарка",
        "баиконурскии": "баиконур", "баиконур": "баиконур", "баиконыр": "баиконур",
    }
    return aliases.get(text, text or None)


def _url(value: str) -> str:
    return normalize_krisha_listing_url(value) or value.strip()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _stats(values: list[float], target_price: float, *, unknown: str | None = None) -> dict[str, Any]:
    if unknown:
        return {"count": 0, "average_price_per_m2": None, "difference_percent": None, "status": unknown}
    if len(values) < MIN_SAMPLES:
        return {"count": len(values), "average_price_per_m2": None, "difference_percent": None, "status": "insufficient_data"}
    average = sum(values) / len(values)
    return {
        "count": len(values),
        "average_price_per_m2": round(average),
        "difference_percent": round((target_price / average - 1) * 100, 1),
        "status": "ok",
    }


def _krisha_reference(value: int | None, target_price: float) -> dict[str, Any]:
    if value is None or value <= 0:
        return {"status": "not_observed", "average_price_per_m2": None, "difference_percent": None}
    return {
        "status": "ok",
        "average_price_per_m2": value,
        "difference_percent": round((target_price / value - 1) * 100, 1),
    }


def observed_krisha_comparison(target: Any, target_price: float) -> dict[str, Any]:
    observed_at = getattr(target, "krisha_comparison_observed_at", None)
    return {
        "source": getattr(target, "krisha_comparison_source", None),
        "source_url": target.source_url,
        "district_label": getattr(target, "krisha_comparison_district", None),
        "observed_at": _utc(observed_at).isoformat() if observed_at is not None else None,
        # The page may not state the period or sample size; neither is inferred.
        "period": None,
        "district": _krisha_reference(getattr(target, "krisha_district_price_per_m2", None), target_price),
        "city": _krisha_reference(getattr(target, "krisha_city_price_per_m2", None), target_price),
    }


def _complex_name(value: str | None) -> str | None:
    text = (value or '').casefold().replace('ё', 'е').strip()
    text = re.sub(r'^\s*(?:жк|жилой\s+комплекс)\s*', '', text)
    text = re.sub(r'[^\w]+', ' ', text).strip()
    return text or None


def _address_key(value: str | None) -> str | None:
    text = re.sub(r'[^\w]+', ' ', (value or '').casefold().replace('ё', 'е')).strip()
    return text or None


def _coordinates(row: Any) -> tuple[float, float] | None:
    lat, lon = getattr(row, 'latitude', None), getattr(row, 'longitude', None)
    if not is_city_coordinate_pair(row.city, lat, lon):
        return None
    return float(lat), float(lon)


def _distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (first[0], first[1], second[0], second[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(min(1.0, a)))


def nearest_comparables(target: Any, rows: Iterable[Any], *, now: datetime) -> dict[str, Any]:
    """Recent comparable saved cards, not a market-wide sample."""
    target_city = city_slug(target.city)
    city_known = target_city is not None
    complex_name = _complex_name(getattr(target, 'residential_complex', None))
    target_coords = _coordinates(target)
    same_complex: list[dict[str, Any]] = []
    nearby: list[dict[str, Any]] = []
    groups = group_listings(rows, target=target)
    for group in groups:
        # A user-marked sold apartment must not be resurrected through another ad.
        if (not city_known or any(member.id == target.id for member in group)
                or any(getattr(member, 'sold_at', None) is not None for member in group)):
            continue
        eligible = []
        for row in group:
            timestamp = getattr(row, 'last_imported_at', None)
            if (availability(row) != 'active' or timestamp is None
                    or not (now - timedelta(days=LOOKBACK_DAYS) <= _utc(timestamp) <= now)
                    or city_slug(row.city) != target_city or not row.price_kzt or row.price_kzt <= 0
                    or not row.area_m2 or not math.isfinite(row.area_m2) or row.area_m2 <= 0
                    or getattr(row, 'rooms', None) is None or getattr(target, 'rooms', None) is None
                    or row.rooms != target.rooms or not target.area_m2 * 0.8 <= row.area_m2 <= target.area_m2 * 1.2):
                continue
            eligible.append(row)
        if not eligible:
            continue
        row = max(eligible, key=lambda candidate: (seller_category(getattr(candidate, 'seller_type', None)) == 'owner',
                                                   _timestamp(candidate), candidate.id))
        url = _url(row.source_url)
        # Preserve the best offer as representative, but don't lose a match
        # solely because that offer omits facts known on another active ad.
        complex_row = row
        if not _complex_name(getattr(row, 'residential_complex', None)) and complex_name:
            complex_row = next((candidate for candidate in eligible
                                if _complex_name(getattr(candidate, 'residential_complex', None)) == complex_name), row)
        location_row = row
        if _coordinates(location_row) is None:
            location_row = next((candidate for candidate in (complex_row, *eligible)
                                 if _coordinates(candidate) is not None), row)
        coords = _coordinates(location_row)
        distance = _distance_m(target_coords, coords) if target_coords and coords else None
        item = {'source_url': url, 'title': getattr(row, 'title', None),
                'address': getattr(row, 'address', None), 'price_kzt': row.price_kzt,
                'price_per_m2': round(row.price_kzt / row.area_m2), 'area_m2': row.area_m2,
                'distance_m': round(distance) if distance is not None else None,
                'location_source_listing_id': location_row.id if coords is not None else None,
                'complex_source_listing_id': complex_row.id if _complex_name(getattr(complex_row, 'residential_complex', None)) else None,
                'listing_id': row.id, 'offer_count': len(unique_offers(group, target=row)),
                'offers': unique_offers(group, target=row)}
        is_same_complex = bool(complex_name and _complex_name(getattr(complex_row, 'residential_complex', None)) == complex_name
                               and (distance is None or distance <= COMPARABLE_RADIUS_M))
        if is_same_complex:
            same_complex.append(item)
        elif distance is not None and distance <= COMPARABLE_RADIUS_M:
            target_address = _address_key(getattr(target, 'address', None))
            row_address = _address_key(getattr(row, 'address', None) or getattr(location_row, 'address', None))
            if target_address and row_address and target_address == row_address:
                continue
            # An almost identical pin with an unknown address may be the same house.
            if distance < 25 and not (target_address and row_address):
                continue
            nearby.append(item)
    nearby.sort(key=lambda item: item['distance_m'])
    status = 'ok' if city_known else 'city_unknown'
    return {
        'lookback_days': LOOKBACK_DAYS, 'radius_m': COMPARABLE_RADIUS_M,
        'area_tolerance_percent': AREA_TOLERANCE_PERCENT,
        'same_complex': {'status': status if complex_name else ('complex_unknown' if city_known else status),
                         'count': len(same_complex), 'items': same_complex},
        'nearby': {'status': status if target_coords else ('coordinates_unknown' if city_known else status),
                   'count': len(nearby), 'items': nearby},
    }


def compare_saved_listings(target: Any, rows: Iterable[Any], *, now: datetime | None = None,
                           include_comparables: bool = False,
                           comparable_rows: Iterable[Any] | None = None) -> dict[str, Any]:
    now = _utc(now or datetime.now(UTC))
    rows = list(rows)
    target_price = target.price_kzt / target.area_m2
    district = _district(target.district)
    target_city = city_slug(target.city)
    city_known = target_city is not None
    city_values: list[float] = []
    district_values: list[float] = []
    seen = {_url(target.source_url)}
    # Keep only the newest observation if the same Krisha listing has legacy URL variants.
    candidates = sorted(
        (row for row in rows if row.last_imported_at is not None),
        key=lambda row: _utc(row.last_imported_at), reverse=True,
    )
    for row in candidates:
        if row.id == target.id or not city_known or city_slug(row.city) != target_city:
            continue
        timestamp = _utc(row.last_imported_at)
        if timestamp < now - timedelta(days=LOOKBACK_DAYS) or timestamp > now:
            continue
        if (
            not row.price_kzt or row.price_kzt <= 0 or not row.area_m2
            or not math.isfinite(row.area_m2) or row.area_m2 <= 0
        ):
            continue
        url = _url(row.source_url)
        if url in seen:
            continue
        seen.add(url)
        value = row.price_kzt / row.area_m2
        city_values.append(value)
        if district and _district(row.district) == district:
            district_values.append(value)
    result = {
        "listing_price_per_m2": round(target_price),
        "as_of": now.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "min_samples": MIN_SAMPLES,
        "source": "saved_listings",
        "krisha": observed_krisha_comparison(target, target_price),
        "district_label": target.district,
        "city_label": CITY_NAMES.get(target_city),
        "city": _stats(city_values, target_price, unknown=None if city_known else "city_unknown"),
        "district": _stats(
            district_values, target_price,
            unknown="city_unknown" if not city_known else (None if district else "district_unknown"),
        ),
    }
    if include_comparables:
        result['comparables'] = nearest_comparables(target, comparable_rows if comparable_rows is not None else rows, now=now)
    return result
