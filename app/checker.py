import asyncio
from dataclasses import dataclass

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.importer import ListingImportError, import_krisha_listing
from app.models import Listing, ListingCheck, PriceSnapshot


@dataclass(slots=True)
class CheckSummary:
    checked: int = 0
    updated: int = 0
    blocked: int = 0
    errors: int = 0


async def check_all_listings() -> CheckSummary:
    summary = CheckSummary()

    async with SessionLocal() as session:
        rows = (await session.scalars(select(Listing).order_by(Listing.id))).all()

    for row in rows:
        summary.checked += 1
        try:
            fresh = await import_krisha_listing(row.source_url)
            old_price = row.price_kzt
            new_price = fresh.price_kzt
            status = "unchanged"
            message = "Объявление доступно, цена без изменений"

            async with SessionLocal() as session:
                current = await session.get(Listing, row.id)
                if current is None:
                    continue

                if new_price != old_price:
                    current.price_kzt = new_price
                    session.add(PriceSnapshot(listing_id=current.id, price_kzt=new_price))
                    status = "price_changed"
                    message = f"Цена изменилась: {old_price} → {new_price} ₸"
                    summary.updated += 1

                session.add(
                    ListingCheck(
                        listing_id=current.id,
                        status=status,
                        message=message,
                        old_price_kzt=old_price,
                        new_price_kzt=new_price,
                    )
                )
                await session.commit()

        except ListingImportError as exc:
            text = str(exc)
            blocked = "провер" in text.lower() or "огранич" in text.lower()
            status = "blocked" if blocked else "error"
            summary.blocked += int(blocked)
            summary.errors += int(not blocked)

            async with SessionLocal() as session:
                session.add(
                    ListingCheck(
                        listing_id=row.id,
                        status=status,
                        message=text,
                        old_price_kzt=row.price_kzt,
                        new_price_kzt=None,
                    )
                )
                await session.commit()

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
            pass

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
