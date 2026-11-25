from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import SessionLocal
from app.krisha_search import normalize_krisha_import_url
from app.models import Listing, SearchResult, TelegramNotification
from app.olx import normalize_olx_listing_url
from app.schemas import ListingCreate
from app.scoring import assess_listing


def apartment_topic(settings) -> dict[str, int]:
    thread_id = getattr(settings, "telegram_apartment_thread_id", None)
    return {"message_thread_id": thread_id} if thread_id is not None else {}


def telegram_timezone(settings) -> tzinfo:
    name = getattr(settings, "telegram_digest_timezone", "Asia/Almaty")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return timezone(timedelta(hours=5)) if name == "Asia/Almaty" else UTC


def _local_time(value: datetime, zone: tzinfo) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local = value.astimezone(zone)
    offset = local.strftime("%z")
    return f"{local:%d.%m.%Y %H:%M} (UTC{offset[:3]}:{offset[3:]})"


async def _listing_dates(session: AsyncSession, listing_id: int) -> tuple[datetime | None, datetime | None]:
    row = (await session.execute(
        select(func.min(SearchResult.first_seen), Listing.created_at)
        .outerjoin(SearchResult, SearchResult.imported_listing_id == Listing.id)
        .where(Listing.id == listing_id)
        .group_by(Listing.id, Listing.created_at)
    )).one_or_none()
    return (row[0], row[1]) if row is not None else (None, None)


def _listing_source_block(
    source_url: str,
    *,
    found_at: datetime | None,
    saved_at: datetime | None,
    sent_at: datetime,
    zone: tzinfo,
    city: str | None = None,
    district: str | None = None,
) -> str:
    if normalize_krisha_import_url(source_url):
        source = "🟨 <b>Krisha.kz</b>"
    elif normalize_olx_listing_url(source_url):
        source = "🟩 <b>OLX.kz</b>"
    else:
        source = "⬜ <b>Другая площадка</b>"
    lines = [" · ".join([source, *(escape(place) for place in (city, district) if place)])]
    if found_at is not None:
        lines.append(f"Найдено {_local_time(found_at, zone)}")
    elif saved_at is not None:
        lines.append(f"Сохранено {_local_time(saved_at, zone)}")
    lines.append(f"Отправлено {_local_time(sent_at, zone)}")
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def _message_for_listing(listing: ListingCreate, source_block: str, *,
                         price_change: tuple[int, int] | None = None) -> str:
    assessment = assess_listing(listing, get_settings())
    title = escape(listing.residential_complex or listing.title)
    lines = [
        f"🏠 <b>{title}</b>",
        source_block,
        f"Цена: <b>{listing.price_kzt:,} ₸</b> · {assessment.price_per_m2:,} ₸/м²".replace(",", " "),
        f"{listing.area_m2:g} м² · {listing.rooms} комн. · этаж {listing.floor or '—'}/{listing.floors_total or '—'}",
        f"Рейтинг: <b>{assessment.score}/100</b> · {assessment.verdict}",
    ]
    if listing.address:
        lines.append(f"Адрес: {escape(listing.address)}")
    elif listing.latitude is not None and listing.longitude is not None:
        lines.append(f"Координаты: {listing.latitude:.6f}, {listing.longitude:.6f}")
    if listing.twogis_name:
        if listing.twogis_rating is not None and listing.twogis_review_count:
            lines.append(
                f"2GIS: {listing.twogis_rating:.1f}/5 · {listing.twogis_review_count} отзывов"
            )
        else:
            lines.append("2GIS: отзывов с оценкой нет")
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

    async with SessionLocal() as session:
        record = await session.scalar(
            select(TelegramNotification).where(TelegramNotification.event_key == event_key)
        )
        if record and record.status == "sent":
            return True
        found_at, saved_at = await _listing_dates(session, listing_id)
        source_block = _listing_source_block(str(listing.source_url), found_at=found_at, saved_at=saved_at,
                                             sent_at=datetime.now(UTC), zone=telegram_timezone(settings),
                                             city=listing.city, district=listing.district)
        message = _message_for_listing(listing, source_block, price_change=price_change)
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
        else:
            record.message = message
        record.attempts = (record.attempts or 0) + 1
        await session.commit()

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": settings.telegram_chat_id,
                    **apartment_topic(settings),
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

async def notify_target_price(*, listing_id: int, offer_id: int, price_kzt: int,
                              target_kzt: int, target_updated_at: datetime,
                              source_url: str, relationship: str) -> bool:
    """Send one target match; unique event claim prevents concurrent duplicate sends."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy import update

    settings = get_settings()
    if not settings.telegram_enabled or not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    version = target_updated_at.isoformat()
    key = f'target_price:{listing_id}:{offer_id}:{price_kzt}:{version}'
    relation = {'self': 'избранное объявление', 'confirmed': 'подтверждённая та же квартира',
                'probable': 'возможно та же квартира'}.get(relationship, 'объявление')
    async with SessionLocal() as session:
        found_at, saved_at = await _listing_dates(session, offer_id)
        source_block = _listing_source_block(source_url, found_at=found_at, saved_at=saved_at,
                                             sent_at=datetime.now(UTC), zone=telegram_timezone(settings))
        prices = f'🎯 Цена достигла цели: <b>{price_kzt:,} ₸</b> (цель {target_kzt:,} ₸)'.replace(',', ' ')
        message = (f'{prices}\n{source_block}\n{relation} · последняя сохранённая цена\n'
                   f'<a href="{escape(source_url, quote=True)}">Открыть объявление</a>')
        session.add(TelegramNotification(event_key=key, event_type='target_price', listing_id=listing_id,
                                         message=message, status='pending', attempts=1))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            claimed = await session.execute(update(TelegramNotification).where(
                TelegramNotification.event_key == key, TelegramNotification.status == 'error'
            ).values(status='pending', attempts=TelegramNotification.attempts + 1, error=None, message=message))
            await session.commit()
            if not claimed.rowcount:
                return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage',
                                         json={'chat_id': settings.telegram_chat_id, **apartment_topic(settings), 'text': message,
                                               'parse_mode': 'HTML', 'disable_web_page_preview': False})
            response.raise_for_status()
            payload = response.json()
            if not payload.get('ok'):
                raise RuntimeError(str(payload.get('description') or 'Telegram отклонил сообщение'))
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        async with SessionLocal() as session:
            await session.execute(update(TelegramNotification).where(TelegramNotification.event_key == key)
                                  .values(status='error', error=str(exc)[:1000]))
            await session.commit()
        return False
    async with SessionLocal() as session:
        await session.execute(update(TelegramNotification).where(TelegramNotification.event_key == key)
                              .values(status='sent', error=None, sent_at=datetime.now(UTC)))
        await session.commit()
    return True
