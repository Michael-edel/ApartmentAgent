"""Comparison with the user's recently imported listings, not a market index."""

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from app.krisha_search import normalize_krisha_listing_url

LOOKBACK_DAYS = 90
MIN_SAMPLES = 5


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


def _is_astana(value: str | None) -> bool:
    text = re.sub(r"[^\w]+", "", (value or "").casefold())
    return text in {"астана", "astana", "нурсултан", "нұрсұлтан"}


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


def compare_saved_listings(target: Any, rows: Iterable[Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = _utc(now or datetime.now(UTC))
    target_price = target.price_kzt / target.area_m2
    district = _district(target.district)
    city_known = _is_astana(target.city)
    city_values: list[float] = []
    district_values: list[float] = []
    seen = {_url(target.source_url)}
    # Keep only the newest observation if the same Krisha listing has legacy URL variants.
    candidates = sorted(
        (row for row in rows if row.last_imported_at is not None),
        key=lambda row: _utc(row.last_imported_at), reverse=True,
    )
    for row in candidates:
        if row.id == target.id or not _is_astana(row.city):
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
    return {
        "listing_price_per_m2": round(target_price),
        "as_of": now.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "min_samples": MIN_SAMPLES,
        "source": "saved_listings",
        "krisha": observed_krisha_comparison(target, target_price),
        "district_label": target.district,
        "city": _stats(city_values, target_price, unknown=None if city_known else "city_unknown"),
        "district": _stats(
            district_values, target_price,
            unknown="city_unknown" if not city_known else (None if district else "district_unknown"),
        ),
    }
