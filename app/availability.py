from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.krisha_search import normalize_krisha_listing_url
from app.models import Listing, ListingCheck, SearchResult
from app.olx import normalize_olx_listing_url, olx_advert_id


SourceAvailability = Literal['active', 'archived', 'unavailable']


def listing_availability(sold_at: datetime | None, source_archived_at: datetime | None,
                         source_unavailable_at: datetime | None = None) -> Literal['active', 'sold', 'archived', 'unavailable']:
    if sold_at is not None:
        return 'sold'
    if source_archived_at is not None:
        return 'archived'
    return 'unavailable' if source_unavailable_at is not None else 'active'


def listing_is_available():
    return and_(Listing.sold_at.is_(None), Listing.source_archived_at.is_(None), Listing.source_unavailable_at.is_(None))


def availability_key(url: str) -> str:
    return normalize_krisha_listing_url(url) or normalize_olx_listing_url(url) or url


async def unavailable_urls(session: AsyncSession) -> set[str]:
    query = select(Listing.source_url).where(~listing_is_available()).union(
        select(SearchResult.url).where(or_(SearchResult.source_archived_at.is_not(None), SearchResult.source_unavailable_at.is_not(None)))
    )
    return {availability_key(url) for url in (await session.scalars(query)).all()}


async def _matching_ids(session: AsyncSession, model, url: str) -> list[int]:
    column = Listing.source_url if model is Listing else SearchResult.url
    canonical = normalize_krisha_listing_url(url)
    query = select(model.id, column).order_by(model.id)
    olx_id = olx_advert_id(url)
    if olx_id:
        rows = (await session.execute(query.where(column.contains(f"{olx_id}.html", autoescape=True)))).all()
        return [row_id for row_id, row_url in rows if olx_advert_id(row_url) == olx_id]
    if canonical is None:
        rows = (await session.execute(query.where(column == url))).all()
        return [row_id for row_id, _ in rows]
    path = canonical.removeprefix('https://krisha.kz')
    rows = (await session.execute(query.where(column.contains(path, autoescape=True)))).all()
    matches = [(row_id, row_url) for row_id, row_url in rows if availability_key(row_url) == canonical]
    matches.sort(key=lambda row: row[1] != canonical)
    return [row_id for row_id, _ in matches]


async def set_listing_sold(session: AsyncSession, source_url: str, sold: bool) -> None:
    ids = await _matching_ids(session, Listing, source_url)
    value = func.coalesce(Listing.sold_at, datetime.now(UTC)) if sold else None
    await session.execute(update(Listing).where(Listing.id.in_(ids)).values(sold_at=value)
                          .execution_options(synchronize_session=False))


async def set_source_availability(session: AsyncSession, source_url: str, *, state: SourceAvailability,
                                  observed_at: datetime | None = None) -> int | None:
    """Record an authoritative observation for all variants of this advert URL."""
    observed_at = observed_at or datetime.now(UTC)
    listing_ids = await _matching_ids(session, Listing, source_url)
    result_ids = await _matching_ids(session, SearchResult, source_url)
    for model, ids in ((Listing, listing_ids), (SearchResult, result_ids)):
        if not ids:
            continue
        archived_at = func.coalesce(model.source_archived_at, observed_at) if state == 'archived' else None
        unavailable_at = func.coalesce(model.source_unavailable_at, observed_at) if state == 'unavailable' else None
        await session.execute(update(model).where(
            model.id.in_(ids),
            or_(model.source_availability_observed_at.is_(None), model.source_availability_observed_at <= observed_at),
        ).values(source_archived_at=archived_at, source_unavailable_at=unavailable_at,
                 source_availability_observed_at=observed_at).execution_options(synchronize_session=False))
    return listing_ids[0] if listing_ids else None


async def restore_verified_check_history(session: AsyncSession) -> None:
    """Recover earlier authoritative checks without interpreting generic failures."""
    inactive = ('archived', 'unavailable')
    had_inactive_check = select(ListingCheck.listing_id).where(ListingCheck.status.in_(inactive))
    checks = (await session.execute(select(
        Listing.source_url, Listing.last_imported_at, ListingCheck.status, ListingCheck.checked_at,
    ).join(ListingCheck).where(
        Listing.id.in_(had_inactive_check),
        ListingCheck.status.in_((*inactive, 'unchanged', 'price_changed')),
    ).order_by(ListingCheck.checked_at.desc(), ListingCheck.id.desc()))).all()
    if not checks:
        return
    known = select(Listing.source_url).where(Listing.source_availability_observed_at.is_not(None)).union(
        select(SearchResult.url).where(SearchResult.source_availability_observed_at.is_not(None))
    )
    seen = {availability_key(url) for url in await session.scalars(known)}
    for url, imported_at, state, checked_at in checks:
        key = availability_key(url)
        if key in seen:
            continue
        seen.add(key)
        # A newer successful import also supersedes an older negative check.
        if state in inactive and (imported_at is None or imported_at <= checked_at):
            await set_source_availability(session, url, state=state, observed_at=checked_at)
