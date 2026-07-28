from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.ai_service import analyze_listing
from app.config import get_settings
from app.database import SessionLocal
from app.models import Listing, PriceSnapshot
from app.schemas import ListingCreate
from app.scoring import assess_listing
from app.telegram import notify_listing


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


async def persist_listing(
    payload: ListingCreate,
    *,
    allow_existing: bool = True,
    notify: bool = True,
    refresh_ai: bool = True,
) -> PersistedListing:
    values = _values(payload)
    now = datetime.now(UTC)
    price_change: tuple[int, int] | None = None

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
            for key, value in values.items():
                if key in {"source_url", "photo_urls"}:
                    continue
                if value is not None:
                    setattr(row, key, value)
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
