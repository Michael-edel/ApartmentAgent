from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_service import analyze_listing
from app.config import get_settings
from app.database import SessionLocal
from app.models import Listing, PriceSnapshot, SearchResult
from app.krisha_search import normalize_krisha_listing_url
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
    raw_url = str(payload.source_url)
    values["source_url"] = normalize_krisha_listing_url(raw_url) or raw_url
    return values


async def _find_listing(session: AsyncSession, source_url: str) -> Listing | None:
    canonical = normalize_krisha_listing_url(source_url)
    exact = await session.scalar(select(Listing).where(Listing.source_url == (canonical or source_url)))
    if exact is not None or canonical is None:
        return exact
    path = canonical.removeprefix("https://krisha.kz")
    candidates = (await session.scalars(
        select(Listing).where(Listing.source_url.contains(path, autoescape=True))
    )).all()
    return next((row for row in candidates if normalize_krisha_listing_url(row.source_url) == canonical), None)


async def _sync_search_results(session: AsyncSession, listing: Listing, now: datetime) -> None:
    """Make every import path expose saved, confirmed fields in search results."""
    canonical = normalize_krisha_listing_url(listing.source_url)
    query = select(SearchResult)
    if canonical:
        path = canonical.removeprefix("https://krisha.kz")
        query = query.where(SearchResult.url.contains(path, autoescape=True))
    else:
        query = query.where(SearchResult.url == listing.source_url)
    for result in (await session.scalars(query)).all():
        if canonical and normalize_krisha_listing_url(result.url) != canonical:
            continue
        result.import_status = "imported"
        result.imported_listing_id = listing.id
        result.import_error = None
        result.imported_at = now
        if listing.mortgage_supported is not None:
            result.mortgage_supported = listing.mortgage_supported
        if listing.bathrooms_count is not None:
            result.bathrooms_count = listing.bathrooms_count
            result.bathrooms_count_is_lower_bound = listing.bathrooms_count_is_lower_bound


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
        existing = await _find_listing(session, source_url)

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
    has_complex_description = bool(payload.complex_description)
    if has_complex_description:
        values['complex_description_observed_at'] = now
    has_krisha_comparison = (
        payload.krisha_district_price_per_m2 is not None
        or payload.krisha_city_price_per_m2 is not None
    )
    if has_krisha_comparison:
        values["krisha_comparison_observed_at"] = now
        values["krisha_comparison_source"] = "krisha_listing_page"
    price_change: tuple[int, int] | None = None
    if twogis_checked:
        values["twogis_checked_at"] = now

    async with SessionLocal() as session:
        row = await _find_listing(session, str(values["source_url"]))
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
            complex_changed = bool(payload.residential_complex) and (
                (payload.residential_complex or '').strip().casefold()
                != (row.residential_complex or '').strip().casefold()
            )
            if has_complex_description or complex_changed:
                # Text and provenance are one snapshot; never attach an old
                # source URL to a new description or a different complex.
                row.complex_description = payload.complex_description
                row.complex_description_source_url = (
                    str(payload.complex_description_source_url)
                    if payload.complex_description_source_url else None
                )
                row.complex_description_source_kind = payload.complex_description_source_kind
                row.complex_description_observed_at = now if has_complex_description else None
            if has_krisha_comparison:
                # Keep a single observed snapshot: an absent member is unknown
                # in this snapshot, rather than an older value with a new date.
                row.krisha_district_price_per_m2 = payload.krisha_district_price_per_m2
                row.krisha_city_price_per_m2 = payload.krisha_city_price_per_m2
                row.krisha_comparison_district = payload.krisha_comparison_district
            if replace_twogis:
                row.twogis_name = payload.twogis_name
                row.twogis_rating = payload.twogis_rating
                row.twogis_review_count = payload.twogis_review_count
                row.twogis_url = payload.twogis_url
            for key, value in values.items():
                if key.startswith('complex_description'):
                    continue
                if key == "krisha_comparison_district" and not has_krisha_comparison:
                    continue
                if key == "bathrooms_count_is_lower_bound" and payload.bathrooms_count is None:
                    continue
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
        await _sync_search_results(session, row, now)
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
