import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.importer import ListingImportError, import_krisha_listing
from app.listing_service import persist_listing
from app.models import Listing, ListingCheck

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CheckSummary:
    checked: int = 0
    updated: int = 0
    blocked: int = 0
    errors: int = 0


class ListingNotFound(ValueError):
    pass


@dataclass(slots=True)
class ListingCheckResult:
    listing_id: int
    status: str
    message: str
    old_price_kzt: int
    new_price_kzt: int | None
    updated: bool = False


async def check_listing(listing_id: int) -> ListingCheckResult:
    """Check one saved listing and persist its latest price snapshot."""

    async with SessionLocal() as session:
        row = await session.get(Listing, listing_id)
        if row is None:
            raise ListingNotFound(f"Listing {listing_id} not found")
        source_url = row.source_url
        old_price = row.price_kzt

    try:
        fresh = await import_krisha_listing(source_url)
    except ListingImportError as exc:
        text = str(exc)
        blocked = "провер" in text.lower() or "огранич" in text.lower()
        status = "blocked" if blocked else "error"
        async with SessionLocal() as session:
            session.add(
                ListingCheck(
                    listing_id=listing_id,
                    status=status,
                    message=text,
                    old_price_kzt=old_price,
                    new_price_kzt=None,
                )
            )
            await session.commit()
        return ListingCheckResult(
            listing_id=listing_id,
            status=status,
            message=text,
            old_price_kzt=old_price,
            new_price_kzt=None,
        )

    saved = await persist_listing(fresh, allow_existing=True, notify=True)
    updated = saved.price_change is not None
    message = (
        f"Цена изменилась: {old_price} → {fresh.price_kzt} ₸"
        if updated
        else "Объявление доступно, цена без изменений"
    )
    status = "price_changed" if updated else "unchanged"

    async with SessionLocal() as session:
        current = await session.get(Listing, listing_id)
        if current is None:
            raise ListingNotFound(f"Listing {listing_id} not found")
        session.add(
            ListingCheck(
                listing_id=current.id,
                status=status,
                message=message,
                old_price_kzt=old_price,
                new_price_kzt=fresh.price_kzt,
            )
        )
        await session.commit()

    return ListingCheckResult(
        listing_id=listing_id,
        status=status,
        message=message,
        old_price_kzt=old_price,
        new_price_kzt=fresh.price_kzt,
        updated=updated,
    )


async def check_all_listings() -> CheckSummary:
    summary = CheckSummary()

    async with SessionLocal() as session:
        listing_ids = list(await session.scalars(select(Listing.id).order_by(Listing.id)))

    for listing_id in listing_ids:
        summary.checked += 1
        try:
            result = await check_listing(listing_id)
            summary.updated += int(result.updated)
            summary.blocked += int(result.status == "blocked")
            summary.errors += int(result.status == "error")
        except ListingNotFound:
            summary.checked -= 1
        except Exception:
            summary.errors += 1
            logger.exception("Listing check failed for %s", listing_id)

        await asyncio.sleep(2)

    return summary


async def periodic_checker(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    interval_seconds = max(settings.check_interval_minutes, 15) * 60

    while not stop_event.is_set():
        try:
            await check_all_listings()
        except Exception:
            # Фоновая задача не должна останавливать API.
            logger.exception("Periodic listing check failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
