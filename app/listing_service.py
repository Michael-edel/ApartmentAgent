from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_service import analyze_listing
from app.author import OWNER_FROM_DESCRIPTION, owner_type_from_description
from app.availability import listing_availability, set_source_availability
from app.config import get_settings
from app.database import SessionLocal
from app.models import Listing, PriceSnapshot, SearchResult
from app.krisha_search import normalize_krisha_listing_url
from app.pledge import parse_pledge_claim, pledge_field
from app.schemas import ListingCreate
from app.repair import parse_repair_description
from app.mortgage import mortgage_value, parse_mortgage_claim
from app.importer import clean_listing_address
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


_REPAIR_FIELDS = ("repair_condition", "repair_source_kind", "repair_evidence")
_MORTGAGE_FIELDS = ("mortgage_supported", "mortgage_source_kind", "mortgage_evidence")
_TEXT_MORTGAGE_SOURCES = {"description", "title"}


def _repair_snapshot(condition, source_kind, description, details, source):
    def explicit(value):
        if not isinstance(value, str):
            return None
        value = value.strip()
        if (not value or value.casefold() in {"-", "—", "не указано", "не важно", "неизвестно", "нет данных", "n/a"}
                or value.replace(".", "", 1).isdigit()):
            return None
        return value[:500]
    condition = explicit(condition)
    if condition and source_kind != "description":
        return condition, "manual" if source == "manual" or source_kind == "manual" else "field", None
    details = details if isinstance(details, dict) else {}
    for label in ("Состояние квартиры", "Ремонт"):
        value = explicit(details.get(label))
        if value:
            return value, "manual" if source == "manual" else "field", None
    found = parse_repair_description(description or "")
    return (found[0], "description", found[1]) if found else (None, None, None)


async def backfill_repair_descriptions() -> int:
    """Re-read only locally stored descriptions; never contact a source site."""
    changed = 0
    async with SessionLocal() as session:
        rows = (await session.scalars(select(Listing).where(or_(
            Listing.repair_condition.is_(None), Listing.repair_source_kind == "description"
        )))).all()
        for row in rows:
            snapshot = _repair_snapshot(row.repair_condition, row.repair_source_kind,
                                        row.description, row.property_details, row.source)
            if snapshot != tuple(getattr(row, key) for key in _REPAIR_FIELDS):
                for key, value in zip(_REPAIR_FIELDS, snapshot):
                    setattr(row, key, value)
                changed += 1
        await session.commit()
    return changed


def _mortgage_snapshot(value, source_kind, description, title, details, source):
    if value is not None and source_kind not in _TEXT_MORTGAGE_SOURCES:
        return value, "manual" if source == "manual" or source_kind == "manual" else "field", None
    details = details if isinstance(details, dict) else {}
    for label in ("Ипотека", "В ипотеку"):
        explicit = mortgage_value(details.get(label))
        if explicit is not None:
            return explicit, "manual" if source == "manual" else "field", None
    description, title = description or "", title or ""
    found = parse_mortgage_claim(description, title=title)
    if found:
        kind = "description" if found[1] in description else "title" if found[1] in title else None
        if kind:
            return found[0], kind, found[1]
    return None, None, None


async def backfill_mortgage_and_addresses() -> dict[str, int]:
    """Enrich stored facts only; no source requests and no price observations."""
    counts = {"mortgage": 0, "address": 0}
    async with SessionLocal() as session:
        for row in (await session.scalars(select(Listing))).all():
            if row.mortgage_supported is None or row.mortgage_source_kind in _TEXT_MORTGAGE_SOURCES:
                snapshot = _mortgage_snapshot(row.mortgage_supported, row.mortgage_source_kind,
                                              row.description, row.title, row.property_details, row.source)
                if snapshot != tuple(getattr(row, key) for key in _MORTGAGE_FIELDS):
                    for key, value in zip(_MORTGAGE_FIELDS, snapshot):
                        setattr(row, key, value)
                    counts["mortgage"] += 1
                    await _sync_search_results(session, row, row.last_imported_at or row.created_at or datetime.now(UTC),
                                               clear_unknown_mortgage=snapshot[0] is None)
            address = clean_listing_address(row.address)
            if address != row.address:
                row.address = address
                counts["address"] += 1
        await session.commit()
    return counts


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


def sync_search_result_facts(result: SearchResult, listing: Listing, *, clear_unknown_mortgage: bool = False) -> None:
    """Replace discovery facts with the currently saved listing's facts."""
    parts = [f"{listing.rooms}-комнатная квартира", f"{listing.area_m2:g} м²"]
    if listing.floor is not None and listing.floors_total is not None:
        parts.append(f"{listing.floor}/{listing.floors_total} этаж")
    parts.append(f"{listing.price_kzt} ₸")
    result.title = " · ".join(parts)
    # Do not keep an old discovery snippet: it can contain obsolete numbers.
    extras = []
    if listing.building_year is not None:
        extras.append(f"год постройки {listing.building_year}")
    if listing.building_type:
        extras.append(listing.building_type)
    result.snippet = ", ".join(extras) or None
    if listing.mortgage_supported is not None or clear_unknown_mortgage:
        result.mortgage_supported = listing.mortgage_supported
    if listing.is_pledged is not None:
        result.is_pledged = listing.is_pledged
    if listing.bathrooms_count is not None:
        result.bathrooms_count = listing.bathrooms_count
        result.bathrooms_count_is_lower_bound = listing.bathrooms_count_is_lower_bound


async def _matching_search_results(session: AsyncSession, source_url: str) -> list[SearchResult]:
    canonical = normalize_krisha_listing_url(source_url)
    query = select(SearchResult)
    if canonical:
        path = canonical.removeprefix("https://krisha.kz")
        query = query.where(SearchResult.url.contains(path, autoescape=True))
    else:
        query = query.where(SearchResult.url == source_url)
    rows = (await session.scalars(query)).all()
    return [row for row in rows if not canonical or normalize_krisha_listing_url(row.url) == canonical]


async def _sync_search_results(session: AsyncSession, listing: Listing, now: datetime, *, clear_unknown_mortgage: bool = False) -> None:
    """Make every import path expose saved, confirmed fields in search results."""
    for result in await _matching_search_results(session, listing.source_url):
        result.import_status = "imported"
        result.imported_listing_id = listing.id
        result.import_error = None
        result.imported_at = now
        sync_search_result_facts(result, listing, clear_unknown_mortgage=clear_unknown_mortgage)


async def backfill_pledge_status() -> int:
    """Apply saved pledge facts without changing other import data."""
    changed = 0
    async with SessionLocal() as session:
        for listing in (await session.scalars(select(Listing))).all():
            if listing.is_pledged is None:
                status = pledge_field(listing.property_details or {})
                if status is None:
                    claim = parse_pledge_claim(listing.description)
                    status = claim[0] if claim else None
                if status is None:
                    continue
                listing.is_pledged = status
                changed += 1
            for result in await _matching_search_results(session, listing.source_url):
                result.is_pledged = listing.is_pledged
        await session.commit()
    return changed


async def backfill_author_types() -> int:
    """Keep explicit author types and recover only declared ownership."""
    changed = 0
    async with SessionLocal() as session:
        listings = (await session.scalars(select(Listing).where(Listing.seller_type.is_(None)))).all()
        for listing in listings:
            seller_type = owner_type_from_description(listing.description)
            if seller_type is not None:
                listing.seller_type = seller_type
                changed += 1
        await session.commit()
    return changed


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
    repair = _repair_snapshot(payload.repair_condition, payload.repair_source_kind,
                              payload.description, payload.property_details, payload.source)
    mortgage = _mortgage_snapshot(payload.mortgage_supported, payload.mortgage_source_kind,
                                  payload.description, payload.title, payload.property_details, payload.source)
    explicit_pledge = pledge_field(payload.property_details)
    if explicit_pledge is None:
        explicit_pledge = payload.is_pledged
    pledge_claim = parse_pledge_claim(payload.description) if explicit_pledge is None else None
    is_pledged = pledge_claim[0] if pledge_claim else explicit_pledge
    explicit_seller_type = (payload.seller_type or "").strip() or None
    if explicit_seller_type == OWNER_FROM_DESCRIPTION:
        explicit_seller_type = None
    seller_type = explicit_seller_type or owner_type_from_description(payload.description)
    payload = payload.model_copy(update={**dict(zip(_REPAIR_FIELDS, repair)), **dict(zip(_MORTGAGE_FIELDS, mortgage)),
                                         "address": clean_listing_address(payload.address), "is_pledged": is_pledged,
                                         "seller_type": seller_type})
    values = _values(payload)
    if payload.details_extracted:
        values["details_imported_at"] = now
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
    mortgage_cleared = False
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
            protect_full_details = (row.details_imported_at is not None or row.repair_source_kind == "description"
                                    or row.mortgage_source_kind in _TEXT_MORTGAGE_SOURCES
                                    or row.seller_type == OWNER_FROM_DESCRIPTION) and not payload.details_extracted and payload.source != "manual"
            if explicit_pledge is None and protect_full_details:
                values.pop("is_pledged", None)
            if explicit_seller_type is not None:
                row.seller_type = explicit_seller_type
            elif (not row.seller_type or row.seller_type == OWNER_FROM_DESCRIPTION) and not protect_full_details:
                if seller_type is not None or payload.description is not None:
                    row.seller_type = seller_type
            if repair[0] is not None:
                trusted_existing = bool(row.repair_condition and row.repair_source_kind != "description")
                if repair[1] != "description" or not (trusted_existing or protect_full_details):
                    for key, value in zip(_REPAIR_FIELDS, repair):
                        setattr(row, key, value)
            elif (row.repair_source_kind == "description" and payload.description is not None
                  and (payload.details_extracted or payload.source == "manual")):
                for key in _REPAIR_FIELDS:
                    setattr(row, key, None)
            if mortgage[0] is not None:
                trusted_existing = row.mortgage_supported is not None and row.mortgage_source_kind not in _TEXT_MORTGAGE_SOURCES
                if mortgage[1] not in _TEXT_MORTGAGE_SOURCES or not (trusted_existing or protect_full_details):
                    for key, value in zip(_MORTGAGE_FIELDS, mortgage):
                        setattr(row, key, value)
            elif (row.mortgage_source_kind in _TEXT_MORTGAGE_SOURCES
                  and (payload.description is not None or row.mortgage_source_kind == "title")
                  and (payload.details_extracted or payload.source == "manual")):
                for key in _MORTGAGE_FIELDS:
                    setattr(row, key, None)
                mortgage_cleared = True
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
                if key in _REPAIR_FIELDS or key in _MORTGAGE_FIELDS:
                    continue
                if key.startswith('complex_description'):
                    continue
                if key == "krisha_comparison_district" and not has_krisha_comparison:
                    continue
                if key == "bathrooms_count_is_lower_bound" and payload.bathrooms_count is None:
                    continue
                if key == "property_details" and not value:
                    continue
                if key == "description" and protect_full_details:
                    continue
                if key == "title" and protect_full_details and row.mortgage_source_kind == "title":
                    continue
                if key in {"source_url", "photo_urls", "twogis_checked_at", "seller_type"}:
                    continue
                if value is not None:
                    setattr(row, key, value)
            if twogis_checked:
                row.twogis_checked_at = now
            if payload.photo_urls and not protect_full_details:
                row.photo_urls = payload.photo_urls
            row.last_imported_at = now
            if old_price != payload.price_kzt:
                price_change = (old_price, payload.price_kzt)
                session.add(_snapshot(row.id, payload.price_kzt, payload.area_m2))
        await _sync_search_results(session, row, now, clear_unknown_mortgage=mortgage_cleared)
        if payload.source in {"krisha-import", "browser"} and payload.availability_status is not None:
            await set_source_availability(session, row.source_url, state=payload.availability_status, observed_at=now)
        await session.commit()
        await session.refresh(row)

    payload = payload.model_copy(update={
        key: getattr(row, key) for key in (*_REPAIR_FIELDS, *_MORTGAGE_FIELDS, "is_pledged", "seller_type")
    })
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

    if notify and listing_availability(row.sold_at, row.source_archived_at, row.source_unavailable_at) == "active":
        # A partial re-import can omit coordinates kept on the stored card.
        # Score the notification with that same location as the displayed card.
        notification_payload = payload.model_copy(update={
            "city": row.city, "latitude": row.latitude, "longitude": row.longitude,
        })
        await notify_listing(
            notification_payload,
            row.id,
            created=created,
            price_change=price_change,
        )
    return PersistedListing(row=row, created=created, price_change=price_change)
