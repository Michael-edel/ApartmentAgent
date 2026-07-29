from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.ai_service import analyze_listing
from app.config import get_settings
from app.database import SessionLocal
from app.models import Listing, PriceSnapshot
from app.schemas import ListingCreate
from app.scoring import assess_listing
from app.telegram import notify_listing
from app.twogis import lookup_twogis


class ListingAlreadyExists(ValueError):
    pass


@dataclass(slots=True)
class PersistedListing:
    row: Listing
    created: bool
    price_change: tuple[int, int] | None = None


def _snapshot(listing_id: int, price_kzt: int, area_m2: float) -> PriceSnapshot:
    return PriceSnapshot(
        listing_id=listing_id,
        price_kzt=price_kzt,
        price_per_m2=round(price_kzt / area_m2),
    )


def _values(payload: ListingCreate) -> dict[str, object]:
    values = payload.model_dump(mode="json")
    values["source_url"] = str(payload.source_url)
    return values


async def _enrich_twogis(
    payload: ListingCreate,
    *,
    now: datetime,
) -> tuple[ListingCreate, bool, bool]:
    """Refresh 2GIS data when the cached lookup is missing or stale.

    The boolean pair is (lookup_attempted, replace_cached_values). A failed
    refresh keeps a previously successful result, while a changed ЖК clears
    the old match so it cannot be shown for the new complex.
    """
    settings = get_settings()
    source_url = str(payload.source_url)
    async with SessionLocal() as session:
        existing = await session.scalar(select(Listing).where(Listing.source_url == source_url))

    if existing is None:
        complex_changed = False
    else:
        if not payload.residential_complex and existing.residential_complex:
            payload = payload.model_copy(
                update={"residential_complex": existing.residential_complex}
            )
        complex_changed = bool(
            payload.residential_complex
            and payload.residential_complex.strip().casefold()
            != (existing.residential_complex or "").strip().casefold()
        )
        if not complex_changed:
            payload = payload.model_copy(
                update={
                    "twogis_name": payload.twogis_name or existing.twogis_name,
                    "twogis_rating": (
                        payload.twogis_rating
                        if payload.twogis_rating is not None
                        else existing.twogis_rating
                    ),
                    "twogis_review_count": (
                        payload.twogis_review_count
                        if payload.twogis_review_count is not None
                        else existing.twogis_review_count
                    ),
                    "twogis_url": payload.twogis_url or existing.twogis_url,
                }
            )

    refresh_before = now - timedelta(hours=max(settings.twogis_refresh_hours, 1))
    should_refresh = bool(
        settings.twogis_enabled
        and settings.twogis_api_key
        and payload.residential_complex
        and (
            existing is None
            or complex_changed
            or existing.twogis_checked_at is None
            or existing.twogis_checked_at < refresh_before
        )
    )
    if not should_refresh:
        return payload, False, complex_changed

    result = await lookup_twogis(
        payload.residential_complex,
        city=payload.city,
        latitude=payload.latitude,
        longitude=payload.longitude,
        settings=settings,
    )
    if result is not None:
        payload = payload.model_copy(update=result.as_listing_fields())
        return payload, True, True
    return payload, True, complex_changed


async def persist_listing(
    payload: ListingCreate,
    *,
    allow_existing: bool = True,
    notify: bool = True,
    refresh_ai: bool = True,
) -> PersistedListing:
    now = datetime.now(UTC)
    payload, twogis_checked, replace_twogis = await _enrich_twogis(payload, now=now)
    values = _values(payload)
    price_change: tuple[int, int] | None = None
    if twogis_checked:
        values["twogis_checked_at"] = now

    async with SessionLocal() as session:
        row = await session.scalar(select(Listing).where(Listing.source_url == values["source_url"]))
        created = row is None
        if row is None:
            row = Listing(**values, last_imported_at=now)
            session.add(row)
            await session.flush()
            session.add(_snapshot(row.id, payload.price_kzt, payload.area_m2))
        else:
            if not allow_existing:
                raise ListingAlreadyExists("Listing with this URL already exists")
            old_price = row.price_kzt
            if replace_twogis:
                row.twogis_name = payload.twogis_name
                row.twogis_rating = payload.twogis_rating
                row.twogis_review_count = payload.twogis_review_count
                row.twogis_url = payload.twogis_url
            for key, value in values.items():
                if key in {"source_url", "photo_urls", "twogis_checked_at"}:
                    continue
                if value is not None:
                    setattr(row, key, value)
            if twogis_checked:
                row.twogis_checked_at = now
            if payload.photo_urls:
                row.photo_urls = payload.photo_urls
            row.last_imported_at = now
            if old_price != payload.price_kzt:
                price_change = (old_price, payload.price_kzt)
                session.add(_snapshot(row.id, payload.price_kzt, payload.area_m2))
        await session.commit()
        await session.refresh(row)

    assessment = assess_listing(payload, get_settings())
    if refresh_ai:
        analysis = await analyze_listing(payload, assessment)
        async with SessionLocal() as session:
            current = await session.get(Listing, row.id)
            if current:
                current.ai_analysis = analysis
                current.ai_analyzed_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(current)
                row = current

    if notify:
        await notify_listing(
            payload,
            row.id,
            created=created,
            price_change=price_change,
        )
    return PersistedListing(row=row, created=created, price_change=price_change)
