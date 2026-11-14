"""Daily asking-price snapshots and observed liquidity of locally saved offers."""
from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from itertools import product
from statistics import mean, median
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import raiseload

from app.cities import CITY_NAMES, city_slug
from app.database import SessionLocal
from app.market_comparison import _complex_name, _district, _utc
from app.models import Listing, MarketIndexSnapshot, PriceSnapshot, SearchResult
from app.olx import olx_advert_id
from app.property_duplicates import _timestamp, availability, canonical_url, group_listings, load_duplicate_decisions
from app.seller import seller_category

router = APIRouter(tags=["market-index"])
logger = logging.getLogger(__name__)
MIN_SAMPLE = 5
ASTANA_TIME = timezone(timedelta(hours=5))
Scope = Literal["city", "district", "complex"]
BuildingType = Literal["brick", "monolith", "panel", "block", "other", "unknown"]
AreaBand = Literal["under_60", "60_79", "80_99", "100_119", "120_plus"]
AREA_BANDS = {"under_60": "До 60 м²", "60_79": "60–79 м²", "80_99": "80–99 м²",
              "100_119": "100–119 м²", "120_plus": "От 120 м²"}
BUILDING_TYPES = {"brick": "Кирпичный", "monolith": "Монолитный", "panel": "Панельный",
                  "block": "Блочный", "other": "Другой", "unknown": "Не указан"}


def _day(now: datetime) -> date:
    return _utc(now).astimezone(ASTANA_TIME).date()


def _building_type(value: str | None) -> BuildingType:
    text = (value or "").casefold().strip()
    if "монолит" in text or text == "monolith":
        return "monolith"
    if "кирп" in text or text == "brick":
        return "brick"
    if "панел" in text or text == "panel":
        return "panel"
    if "блоч" in text or text == "block":
        return "block"
    return "other" if text else "unknown"


def _area_band(area: float) -> AreaBand:
    for upper, band in ((60, "under_60"), (80, "60_79"), (100, "80_99"), (120, "100_119")):
        if area < upper:
            return band
    return "120_plus"


def _percentile(sorted_values: list[float], fraction: float) -> int:
    index = (len(sorted_values) - 1) * fraction
    left = int(index)
    right = min(left + 1, len(sorted_values) - 1)
    return round(sorted_values[left] + (sorted_values[right] - sorted_values[left]) * (index - left))


def _quartiles(values: list[float]) -> dict:
    if len(values) < MIN_SAMPLE:
        return {"sample_size": len(values), "median": None, "p25": None, "p75": None}
    ordered = sorted(values)
    return {"sample_size": len(values), "median": _percentile(ordered, .5),
            "p25": _percentile(ordered, .25), "p75": _percentile(ordered, .75)}


def _representative(rows: list[Listing]) -> Listing:
    return max(rows, key=lambda row: (seller_category(row.seller_type) == "owner", _timestamp(row), row.id))


def _valid_price(row: Listing) -> bool:
    return bool(row.price_kzt and row.price_kzt > 0 and row.area_m2 and math.isfinite(row.area_m2) and row.area_m2 > 0)


def _snapshot_rows(rows: list[Listing], decisions: dict, now: datetime) -> list[dict]:
    values = defaultdict(list)
    labels = {("city", ""): CITY_NAMES["astana"]}
    baseline = ("city", "", "", 0, "")
    values[baseline] = []
    for group in group_listings(rows, decisions=decisions):
        if any(row.sold_at is not None for row in group):
            continue
        eligible = [row for row in group if availability(row) == "active" and _valid_price(row)
                    and _utc(row.created_at) <= now]
        if not eligible:
            continue
        row = _representative(eligible)
        locations = [("city", "")]
        for scope, field, normalize in (("district", "district", _district),
                                        ("complex", "residential_complex", _complex_name)):
            # Another active offer can supply the same property's missing location.
            location_label = getattr(row, field)
            if not normalize(location_label):
                alternative = next((other for other in eligible if normalize(getattr(other, field))), None)
                location_label = getattr(alternative, field) if alternative else None
            if location_key := normalize(location_label):
                locations.append((scope, location_key[:200]))
                labels.setdefault(locations[-1], location_label[:200])
        dimensions = product(("", _building_type(row.building_type)), (0, row.rooms), ("", _area_band(row.area_m2)))
        for building, rooms, area in dimensions:
            for scope, location in locations:
                values[(scope, location, building, rooms, area)].append(row.price_kzt / row.area_m2)
    return [dict(day=_day(now), city="astana", scope=scope, location=location, building_type=building,
                 rooms=rooms, area_band=area, location_label=labels[(scope, location)], observed_at=now,
                 **_quartiles(points))
            for (scope, location, building, rooms, area), points in values.items()]


async def record_market_snapshot(*, now: datetime | None = None) -> int:
    """Store the first complete observation of the current Astana calendar day."""
    observed = _utc(now or datetime.now(UTC))
    baseline = (_day(observed), "astana", "city", "", "", 0, "")
    async with SessionLocal() as session, session.begin():
        if await session.get(MarketIndexSnapshot, baseline) is not None:
            return 0
        rows = [row for row in await session.scalars(select(Listing).options(raiseload("*")))
                if city_slug(row.city) == "astana"]
        snapshots = _snapshot_rows(rows, await load_duplicate_decisions(session), observed)
        insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
        # The city total is the unique reservation for the entire daily snapshot.
        reserved = await session.scalar(insert(MarketIndexSnapshot).values(snapshots[0])
            .on_conflict_do_nothing().returning(MarketIndexSnapshot.day))
        if reserved is None:
            return 0
        for start in range(1, len(snapshots), 50):
            await session.execute(insert(MarketIndexSnapshot).values(snapshots[start:start + 50]))
        return len(snapshots)


async def periodic_market_index(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await record_market_snapshot()
        except Exception:
            logger.exception("Could not save market index snapshot")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=900)
        except TimeoutError:
            continue


def _start_day(now: datetime, days: int) -> date:
    return _day(now) - timedelta(days=days - 1)


async def _series(session, *, now: datetime, days: int, scope: Scope, location: str,
                  building_type: str = "", rooms: int = 0, area_band: str = "") -> list[dict]:
    query = select(MarketIndexSnapshot).where(
        MarketIndexSnapshot.city == "astana", MarketIndexSnapshot.scope == scope,
        MarketIndexSnapshot.location == location, MarketIndexSnapshot.building_type == building_type,
        MarketIndexSnapshot.rooms == rooms, MarketIndexSnapshot.area_band == area_band,
        MarketIndexSnapshot.day.between(_start_day(now, days), _day(now)),
    ).order_by(MarketIndexSnapshot.day)
    return [{"day": row.day.isoformat(), "sample_size": row.sample_size,
             "median": row.median, "p25": row.p25, "p75": row.p75}
            for row in await session.scalars(query)]


@router.get("/api/v1/market-index")
async def market_index(city: Literal["astana"] = "astana", scope: Scope = "city",
                       location: str | None = Query(default=None, max_length=200),
                       building_type: BuildingType | None = None, rooms: int | None = Query(default=None, ge=1),
                       area_band: AreaBand | None = None, days: int = Query(default=90, ge=1, le=365)) -> dict:
    now = datetime.now(UTC)
    key = ""
    if scope == "district":
        key = _district(location) or ""
    elif scope == "complex":
        key = _complex_name(location) or ""
    async with SessionLocal() as session:
        series = await _series(session, now=now, days=days, scope=scope, location=key,
                               building_type=building_type or "", rooms=rooms or 0, area_band=area_band or "")
        locations = {}
        if scope != "city":
            query = select(MarketIndexSnapshot.location, MarketIndexSnapshot.location_label).where(
                MarketIndexSnapshot.city == city, MarketIndexSnapshot.scope == scope,
                MarketIndexSnapshot.day.between(_start_day(now, days), _day(now)),
                MarketIndexSnapshot.building_type == "", MarketIndexSnapshot.rooms == 0,
                MarketIndexSnapshot.area_band == "",
            ).order_by(MarketIndexSnapshot.day.desc())
            for segment, label in (await session.execute(query)).all():
                locations.setdefault(segment, label)
        room_options = (await session.scalars(select(MarketIndexSnapshot.rooms).where(
            MarketIndexSnapshot.city == city, MarketIndexSnapshot.scope == "city",
            MarketIndexSnapshot.rooms > 0, MarketIndexSnapshot.building_type == "", MarketIndexSnapshot.area_band == "",
            MarketIndexSnapshot.day.between(_start_day(now, days), _day(now)),
        ).distinct().order_by(MarketIndexSnapshot.rooms))).all()
    return {"city": city, "scope": scope, "location": key or None,
            "label": CITY_NAMES[city] if scope == "city" else locations.get(key) or location,
            "building_type": building_type, "rooms": rooms, "area_band": area_band,
            "series": series, "currency": "KZT", "min_sample": MIN_SAMPLE,
            "locations": [{"key": key, "label": label} for key, label in sorted(locations.items(), key=lambda item: item[1])],
            "area_bands": [{"key": key, "label": label} for key, label in AREA_BANDS.items()],
            "building_types": [{"key": key, "label": label} for key, label in BUILDING_TYPES.items()],
            "room_options": room_options}


def _source_key(url: str) -> tuple[str, str]:
    advert = olx_advert_id(url)
    return ("olx", advert) if advert else ("url", canonical_url(url))


def _first_seen(row: Listing, seen: dict) -> datetime:
    created = _utc(row.created_at)
    return min(created, seen.get(_source_key(row.source_url), created))


def _days_observed(first: datetime, last: datetime) -> int:
    return max(0, (last - first).days)


def _last_active_observation(row: Listing, last_seen: dict, now: datetime) -> datetime:
    dates = (row.created_at, row.last_imported_at, row.source_availability_observed_at,
             last_seen.get(_source_key(row.source_url)))
    return max((_utc(value) for value in dates if value is not None and _utc(value) <= now), default=_utc(row.created_at))


def _ended(row: Listing) -> tuple[str, datetime] | None:
    status = availability(row)
    timestamp = {"sold": row.sold_at, "archived": row.source_archived_at,
                 "unavailable": row.source_unavailable_at}.get(status)
    return (status, _utc(timestamp)) if timestamp else None


def _cohort(grouped: list[list[Listing]], target: Listing, first_seen: dict, last_seen: dict,
            now: datetime, window: int) -> dict:
    cutoff = now - timedelta(days=window)
    district = _district(target.district)
    counts = {"sold": 0, "archived": 0, "unavailable": 0}
    durations = []
    sample_size = 0
    for group in grouped:
        if not district or any(row.id == target.id for row in group):
            continue
        comparable = any(_district(row.district) == district and row.rooms == target.rooms
                         and target.area_m2 * .8 <= row.area_m2 <= target.area_m2 * 1.2 for row in group)
        if not comparable:
            continue
        ended = [event for row in group if (event := _ended(row)) is not None and event[1] <= now]
        sold = [event for event in ended if event[0] == "sold"]
        active = [row for row in group if availability(row) == "active"]
        # One active duplicate means the apartment is still available, unless sold was recorded.
        end = min(sold, key=lambda event: event[1]) if sold else (
            max(ended, key=lambda event: event[1]) if ended and not active else None)
        if end:
            if end[1] < cutoff:
                continue
            counts[end[0]] += 1
            first = min(_first_seen(row, first_seen) for row in group)
            if first <= end[1]:
                durations.append(_days_observed(first, end[1]))
        else:
            if not any(cutoff <= _last_active_observation(row, last_seen, now) <= now for row in active):
                continue
        sample_size += 1
    ended_count = sum(counts.values())
    removed = counts["archived"] + counts["unavailable"]
    return {"window_days": window, "sample_size": sample_size, "ended_count": ended_count,
            "removed_count": removed, "ended_fraction": round(ended_count / sample_size, 4) if sample_size else None,
            "removed_fraction": round(removed / sample_size, 4) if sample_size else None,
            "mean_ended_observed_days": round(mean(durations), 1) if durations else None,
            "ended_duration_sample_size": len(durations),
            **{f"{status}_count": count for status, count in counts.items()},
            **{f"{status}_fraction": round(count / sample_size, 4) if sample_size else None
               for status, count in counts.items()}}


def _liquidity_signal(target: Listing, observed_days: int, cuts: list[int], percent: float | None, cohort: dict) -> dict | None:
    if availability(target) != "active" or cohort["ended_duration_sample_size"] < MIN_SAMPLE:
        return None
    duration = cohort["mean_ended_observed_days"]
    if observed_days >= max(30, duration * 1.5):
        if len(cuts) >= 2 and percent is not None and percent >= 3:
            return {"code": "negotiable", "label": "Вероятно, можно торговаться",
                    "reason": "Длительное наблюдение и минимум два снижения на сумму от 3% первой цены."}
        return {"code": "long", "label": "Долго висит",
                "reason": "Срок наблюдения от 30 дней и в полтора раза выше среднего срока завершённых аналогов."}
    if duration <= 14 and observed_days <= 14 and cohort["ended_fraction"] >= .5:
        return {"code": "fast", "label": "Быстро уходит",
                "reason": "Как минимум половина наблюдаемых аналогов завершилась, средний срок завершённых до 14 дней."}
    return None


@router.get("/api/v1/listings/{listing_id}/market-history")
async def listing_market_history(listing_id: int) -> dict:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        target = await session.get(Listing, listing_id, options=[raiseload("*")])
        if target is None:
            raise HTTPException(404, "Карточка квартиры не найдена")
        city = city_slug(target.city)
        district = _district(target.district)
        city_series = await _series(session, now=now, days=90, scope="city", location="") if city == "astana" else []
        district_series = (await _series(session, now=now, days=90, scope="district", location=district)
                           if city == "astana" and district else [])
        snapshots = list(await session.scalars(select(PriceSnapshot).where(
            PriceSnapshot.listing_id == listing_id, PriceSnapshot.observed_at <= now,
        ).order_by(PriceSnapshot.observed_at, PriceSnapshot.id)))
        rows = [row for row in await session.scalars(select(Listing).options(raiseload("*")))
                if city and city_slug(row.city) == city]
        grouped = group_listings(rows, target=target, decisions=await load_duplicate_decisions(session)) if rows else []
        seen = (await session.execute(select(SearchResult.url, SearchResult.first_seen, SearchResult.last_seen))).all()

    first_seen, last_seen = {}, {}
    for url, first, last in seen:
        key = _source_key(url)
        if first is not None:
            first_seen[key] = min(_utc(first), first_seen.get(key, now))
        if last is not None and _utc(last) <= now:
            last_seen[key] = max(_utc(last), last_seen.get(key, _utc(last)))
    first = _first_seen(target, first_seen)
    end = _ended(target)
    last = _last_active_observation(target, last_seen, now)
    if snapshots:
        last = max(last, _utc(snapshots[-1].observed_at))
    last = min(now, end[1] if end else last)
    observed_days = _days_observed(first, last)
    prices = [row.price_kzt for row in snapshots]
    changes = [old - new for old, new in zip(prices, prices[1:]) if old != new]
    cuts = [change for change in changes if change > 0]
    reduction_percent = round(sum(cuts) / prices[0] * 100, 1) if prices and prices[0] > 0 else None
    cohorts = {str(window): _cohort(grouped, target, first_seen, last_seen, now, window) for window in (30, 90)}
    recent = [item for item in district_series if item["day"] >= _start_day(now, 30).isoformat()
              and item["median"] is not None]
    district_median = round(median(item["median"] for item in recent)) if recent else None
    price_per_m2 = round(target.price_kzt / target.area_m2) if _valid_price(target) else None
    return {"listing_id": target.id, "currency": "KZT", "min_sample": MIN_SAMPLE,
            "chart_window": {"start_day": _start_day(now, 30).isoformat(), "end_day": _day(now).isoformat(),
                             "utc_offset_minutes": 300},
            "price_history": [{"observed_at": _utc(row.observed_at).isoformat(), "price_kzt": row.price_kzt,
                               "price_per_m2": row.price_per_m2} for row in snapshots],
            "city": {"label": CITY_NAMES.get(city) or target.city, "series": city_series},
            "district": {"label": target.district, "series": district_series},
            "district_comparison": {"window_days": 30, "median_price_per_m2": district_median,
                "listing_price_per_m2": price_per_m2, "sample_days": len(recent),
                "latest_day": recent[-1]["day"] if recent else None,
                "difference_percent": round((price_per_m2 / district_median - 1) * 100, 1)
                if district_median and price_per_m2 is not None else None},
            "liquidity": {"first_seen_at": first.isoformat(), "last_observed_at": last.isoformat(),
                "observed_days": observed_days,
                "price_change_count": len(changes), "price_reduction_count": len(cuts),
                "total_reduction_kzt": sum(cuts), "total_reduction_percent": reduction_percent,
                "cohorts": cohorts, "signal": _liquidity_signal(target, observed_days, cuts, reduction_percent, cohorts["90"])
                if last >= now - timedelta(days=7) else None}}
