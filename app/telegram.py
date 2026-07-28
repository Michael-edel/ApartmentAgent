from __future__ import annotations

from datetime import UTC, datetime
from html import escape

import httpx
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import TelegramNotification
from app.schemas import ListingCreate
from app.scoring import assess_listing


def _message_for_listing(listing: ListingCreate, *, price_change: tuple[int, int] | None = None) -> str:
    assessment = assess_listing(listing, get_settings())
    title = escape(listing.residential_complex or listing.title)
    lines = [
        f"🏠 <b>{title}</b>",
        f"Цена: <b>{listing.price_kzt:,} ₸</b> · {assessment.price_per_m2:,} ₸/м²".replace(",", " "),
        f"{listing.area_m2:g} м² · {listing.rooms} комн. · этаж {listing.floor or '—'}/{listing.floors_total or '—'}",
        f"Рейтинг: <b>{assessment.score}/100</b> · {assessment.verdict}",
    ]
    if listing.district or listing.residential_complex:
        lines.append(
            " · ".join(escape(item) for item in (listing.district, listing.residential_complex) if item)
        )
    if price_change:
        old, new = price_change
        direction = "↓" if new < old else "↑"
        lines.append(f"{direction} Цена изменилась: {old:,} → {new:,} ₸".replace(",", " "))
    lines.append(f'<a href="{escape(str(listing.source_url), quote=True)}">Открыть объявление</a>')
    return "\n".join(lines)


async def notify_listing(
    listing: ListingCreate,
    listing_id: int,
    *,
    created: bool,
    price_change: tuple[int, int] | None = None,
) -> bool:
    settings = get_settings()
    if not settings.telegram_enabled or not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    assessment = assess_listing(listing, settings)
    if created and assessment.score < settings.telegram_notify_min_score:
        return False
    if not created and price_change is None:
        return False

    event_type = "new_listing" if created else "price_change"
    change = price_change or (0, 0)
    suffix = "new" if created else f"{change[0]}-{change[1]}"
    event_key = f"{event_type}:{listing_id}:{suffix}"
    message = _message_for_listing(listing, price_change=price_change)

    async with SessionLocal() as session:
        record = await session.scalar(
            select(TelegramNotification).where(TelegramNotification.event_key == event_key)
        )
        if record and record.status == "sent":
            return True
        if record is None:
            record = TelegramNotification(
                event_key=event_key,
                event_type=event_type,
                listing_id=listing_id,
                message=message,
                status="pending",
            )
            session.add(record)
            await session.flush()
        record.attempts = (record.attempts or 0) + 1
        await session.commit()

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(str(payload.get("description") or "Telegram отклонил сообщение"))
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        async with SessionLocal() as session:
            failed = await session.scalar(
                select(TelegramNotification).where(TelegramNotification.event_key == event_key)
            )
            if failed:
                failed.status = "error"
                failed.error = str(exc)[:1000]
                await session.commit()
        return False

    async with SessionLocal() as session:
        sent = await session.scalar(
            select(TelegramNotification).where(TelegramNotification.event_key == event_key)
        )
        if sent:
            sent.status = "sent"
            sent.error = None
            sent.sent_at = datetime.now(UTC)
            await session.commit()
    return True


async def telegram_status() -> dict[str, object]:
    settings = get_settings()
    async with SessionLocal() as session:
        sent = await session.scalar(
            select(func.count(TelegramNotification.id)).where(TelegramNotification.status == "sent")
        )
    return {
        "enabled": settings.telegram_enabled,
        "configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "sent_notifications": sent or 0,
    }
