"""Independent commercial-property watcher; first successful scan is a baseline."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import escape

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import SessionLocal
from app.import_observability import SourceCoolingDown, observed_fetch
from app.krisha_search import (
    KrishaSearchBlocked, KrishaSearchItem, _security_text, normalize_krisha_listing_url,
    parse_krisha_search_page,
)
from app.models import CommercialEvent, CommercialListing, CommercialMonitorPreference, CommercialScan
from app.olx import _get_olx, _number, _state, normalize_olx_listing_url, olx_advert_id
from app.source_photos import public_listing_photo
from app.telegram import _local_time, telegram_timezone

log = logging.getLogger(__name__)
KRISHA_CATALOGS = {
    ('property', 'rent'): 'https://krisha.kz/arenda/kommercheskaya-nedvizhimost/ekibastuz/',
    ('property', 'sale'): 'https://krisha.kz/prodazha/kommercheskaya-nedvizhimost/ekibastuz/',
    ('business', 'sale'): 'https://krisha.kz/prodazha/biznes/ekibastuz/',
    ('industry', 'rent'): 'https://krisha.kz/arenda/prombazy/ekibastuz/',
    ('industry', 'sale'): 'https://krisha.kz/prodazha/prombazy/ekibastuz/',
}
SECTION_LABELS = {'property': 'Коммерческая недвижимость', 'business': 'Готовый бизнес',
                  'industry': 'Промбазы и заводы'}
AREA_NUMBER = r'(\d{1,3}(?:\s\d{3})+|\d+)([.,]\s*\d+)?\s*(?:м²|м2\b|кв\.?\s*м\b)'
AREA = re.compile(AREA_NUMBER, re.I)
BUILDING = r'(?:помещени(?:е|я|ем|й)|здани(?:е|я|ем|й)|склад(?:а|ом)?|ангар(?:а|ом)?|цех(?:а|ом)?|бокс(?:а|ом)?)'
BUILDING_AREA = re.compile(
    rf'\b(?:площадь\s+{BUILDING}|{BUILDING}(?:\s+площадью)?)\s*[:—–-]?\s*{AREA_NUMBER}', re.I)
KRISHA_PRICE = re.compile(
    r'^(?:от\s+)?\d[\d\s.,]*\s*(?:₸|тг\b)'
    r'(?:\s*(?:за\s+|в\s+|/\s*)?(?:вс[её]\b|месяц\b|мес\b\.?|сутки\b|день\b|час\b|м²|м2\b|кв\.?\s*м\b))?',
    re.I,
)
OLX_ROUTES = {'rent': ('arenda', 11), 'sale': ('prodazha', 14)}
KRISHA_COOLDOWN_SECONDS = 60 * 60
PHOTO_BATCH_SIZE = 3
PHOTO_RETRY_DELAY = timedelta(hours=6)
KRISHA_IMAGE_VARIANT = re.compile(r'(?:-(?:\d+x\d+|full))?\.(?:jpe?g|png|webp)$', re.I)
LEGACY_DETECTION_OFFSET = re.compile(
    r'^(Обнаружено \d{2}\.\d{2}\.\d{4} \d{2}:\d{2}) \(UTC[+-]\d{2}:\d{2}\)$', re.MULTILINE,
)
scan_lock = asyncio.Lock()
delivery_lock = asyncio.Lock()


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _latest(values) -> datetime | None:
    return max((_utc(value) for value in values if value is not None), default=None)


def krisha_primary_price(value: str | None) -> str | None:
    # The catalog's price container can also include a separate price per square metre.
    match = KRISHA_PRICE.match(' '.join((value or '').split()))
    return match.group().strip() if match else None


async def _monitor_enabled(session: AsyncSession, settings) -> bool:
    preference = await session.get(CommercialMonitorPreference, 1)
    return preference.enabled if preference is not None else settings.commercial_monitor_enabled


async def commercial_monitor_enabled(settings) -> bool:
    async with SessionLocal() as session:
        return await _monitor_enabled(session, settings)


async def set_commercial_monitor_enabled(enabled: bool) -> None:
    async with scan_lock, SessionLocal() as session, session.begin():
        preference = await session.get(CommercialMonitorPreference, 1)
        if preference is None:
            session.add(CommercialMonitorPreference(id=1, enabled=enabled))
        else:
            preference.enabled = enabled


async def start_commercial_tracking() -> datetime:
    """Set the discovery baseline once and mark all already saved listings as known."""
    async with scan_lock, SessionLocal() as session, session.begin():
        preference = await session.get(CommercialMonitorPreference, 1)
        if preference is None:
            preference = CommercialMonitorPreference(id=1, enabled=get_settings().commercial_monitor_enabled)
            session.add(preference)
        if preference.tracking_started_at is not None:
            return _utc(preference.tracking_started_at)
        started_at = datetime.now(UTC)
        preference.tracking_started_at = started_at
        await session.execute(update(CommercialListing).values(detected_at=started_at))
    return started_at


async def commercial_monitor_status(settings) -> dict:
    async with SessionLocal() as session:
        preference = await session.get(CommercialMonitorPreference, 1)
        enabled = preference.enabled if preference is not None else settings.commercial_monitor_enabled
        tracking_started_at = preference.tracking_started_at if preference else None
        scans = (await session.scalars(select(CommercialScan).where(CommercialScan.initialized))).all()
        sources = {}
        for source, prefix in (('krisha', 'https://krisha.kz/'), ('olx', 'olx:')):
            count, last_seen = (await session.execute(
                select(func.count(), func.max(CommercialListing.seen_at))
                .where(CommercialListing.url.startswith(prefix))
            )).one()
            source_scans = [scan for scan in scans if scan.deal_type.startswith('olx-') == (source == 'olx')]
            last_check = _latest(scan.last_checked_at for scan in source_scans)
            if last_check is None and source_scans:
                # Older releases only recorded the first scan and when listings were seen.
                last_check = _latest([last_seen, *(scan.started_at for scan in source_scans)])
            sources[source] = {'count': count, 'last_checked_at': last_check}
    return {
        'enabled': enabled,
        'last_checked_at': _latest(source['last_checked_at'] for source in sources.values()),
        'sources': sources,
        'tracking_started_at': _utc(tracking_started_at) if tracking_started_at else None,
    }


@dataclass(slots=True)
class OlxCommercialItem:
    title: str
    url: str
    snippet: str | None
    area: float
    published_at: datetime | None
    photo_url: str | None = None


@dataclass(slots=True)
class KrishaCommercialItem(KrishaSearchItem):
    photo_urls: list[str] = field(default_factory=list)


def krisha_gallery(item) -> list[str]:
    if not isinstance(item, KrishaCommercialItem):
        return []
    photos = {}
    for value in item.photo_urls:
        photo = public_listing_photo(value, 'krisha')
        if photo:
            key = KRISHA_IMAGE_VARIANT.sub('', photo.split('?', 1)[0])
            photos.setdefault(key, photo)
    return list(photos.values())


def eligible(item, section='property'):
    if isinstance(item, OlxCommercialItem):
        return (item.area, item.snippet) if item.area >= 100 else None
    if section == 'industry':
        match = BUILDING_AREA.search(item.title) or BUILDING_AREA.search(item.description or '')
    else:
        match = AREA.search(item.title) or BUILDING_AREA.search(item.description or '')
    if not match:
        return None
    whole = ''.join(match.group(1).split())
    fraction = ''.join((match.group(2) or '').split()).replace(',', '.')
    area = float(whole + fraction)
    if area < 100:
        return None
    return area, krisha_primary_price(item.snippet)


async def krisha_photo_candidates(items, section) -> list[str]:
    """Give new/changed cards priority, and work through unchecked galleries in batches."""
    prices = {}
    for item in items:
        facts = eligible(item, section)
        if facts is not None and not krisha_gallery(item):
            prices[item.url] = facts[1]
    if not prices:
        return []

    async with SessionLocal() as session:
        rows = await session.execute(
            select(CommercialListing.url, CommercialListing.price, CommercialListing.photo_url,
                   CommercialListing.photo_urls, CommercialListing.photo_checked_at)
            .where(CommercialListing.url.in_(prices))
        )
        known = {row.url: row for row in rows}

    earliest_retry = datetime.now(UTC) - PHOTO_RETRY_DELAY
    never_attempted = datetime.min.replace(tzinfo=UTC)
    candidates = []
    for url, price in prices.items():
        row = known.get(url)
        if row is None:
            candidates.append((0, never_attempted, url))
            continue
        if row.photo_urls:
            continue
        attempted_at = _utc(row.photo_checked_at) if row.photo_checked_at else never_attempted
        if attempted_at > earliest_retry:
            continue
        old_price = krisha_primary_price(row.price)
        changed = bool(old_price and price and old_price != price)
        priority = 1 if changed else 2 + bool(row.photo_url)
        candidates.append((priority, attempted_at, url))
    candidates.sort(key=lambda entry: entry[:2])
    return [url for _, _, url in candidates[:PHOTO_BATCH_SIZE]]


async def fetch_catalog(client, url, section='property'):
    rows = {}
    for page in range(1, 11):
        response = await client.get(url) if page == 1 else await client.get(url, params={'page': page})
        if response.status_code in (403, 429, 468):
            raise KrishaSearchBlocked(f'Krisha ограничила доступ: HTTP {response.status_code}')
        response.raise_for_status()
        if any(x in _security_text(response.text) for x in ('captcha', 'проверка безопасности', 'cloudflare', 'access denied')):
            raise KrishaSearchBlocked('Krisha запросила проверку безопасности')
        parsed = parse_krisha_search_page(response.text, expected_city='ekibastuz')
        if not parsed:
            if page == 1:
                raise RuntimeError('Не удалось прочитать каталог Krisha; базовая выборка не изменена')
            break
        for item in parsed:
            if eligible(item, section):
                rows[item.url] = item
        # Some pages can have no eligible premises, so compare all parsed URLs.
        ids = {item.url for item in parsed}
        if page > 1 and ids.issubset(seen):
            break
        seen = ids if page == 1 else seen | ids
        if page < 10:
            await asyncio.sleep(2)
    else:
        log.warning('Commercial catalog reached the 10-page scan limit: %s', url)
    return list(rows.values())


def parse_olx_catalog(html, category):
    data = _state(html).get('listing', {}).get('listing', {})
    ads = data.get('ads')
    if not isinstance(ads, list):
        raise ValueError('Неверный формат коммерческой выдачи OLX')
    items = {}
    for ad in ads:
        if (not isinstance(ad, dict) or (ad.get('location') or {}).get('cityNormalizedName') != 'ekibastuz'
                or (ad.get('category') or {}).get('id') != category):
            continue
        url = normalize_olx_listing_url(ad.get('url') or '')
        title = str(ad.get('title') or '').strip()
        fields = {p.get('key'): p.get('normalizedValue') for p in ad.get('params') or [] if isinstance(p, dict)}
        area = _number(fields.get('total_area'))
        if not url or not title or area is None or area < 100:
            continue
        price_data = (ad.get('price') or {}).get('regularPrice') or {}
        amount = _number(price_data.get('value')) if price_data.get('currencyCode') == 'KZT' else None
        price = f'{amount:,.0f} ₸'.replace(',', ' ') if amount is not None else None
        try:
            published_at = datetime.fromisoformat(ad.get('createdTime') or '')
            if published_at.tzinfo is None:
                published_at = None
        except (ValueError, TypeError):
            published_at = None
        photos = ad.get('photos')
        photo_url = None
        if isinstance(photos, list):
            photo_url = next((photo for value in photos if (photo := public_listing_photo(value, 'olx'))), None)
        items[olx_advert_id(url)] = OlxCommercialItem(title[:300], url, price, area, published_at, photo_url)
    return items, max(1, int(data.get('totalPages') or 1))


async def fetch_olx_catalog(deal):
    slug, category = OLX_ROUTES[deal]
    url = f'https://www.olx.kz/nedvizhimost/kommercheskie-pomeshcheniya/{slug}/ekibastuz/'
    rows = {}
    for page in range(1, 11):
        response = await _get_olx(url if page == 1 else f'{url}?page={page}')
        items, pages = parse_olx_catalog(response.text, category)
        rows.update(items)
        if page >= pages:
            break
        await asyncio.sleep(2)
    return list(rows.values())


def event_message(item, deal, area, price, old_price=None, *, section='property', detected_at=None):
    kind = 'Аренда' if deal == 'rent' else 'Продажа'
    header = 'Цена изменилась' if old_price else 'Впервые найдено' if detected_at else 'Новое объявление'
    price_line = f'{escape(old_price)} → {escape(price)}' if old_price and price else escape(price or 'не указана')
    source = 'OLX' if isinstance(item, OlxCommercialItem) else 'Krisha'
    detected = f'Обнаружено {_local_time(detected_at, telegram_timezone(get_settings()))}\n' if detected_at else ''
    return (f'🏢 <b>{SECTION_LABELS[section]} · {kind}</b>\n{header} · {source} · Экибастуз\n{escape(item.title)}\n'
            f'Площадь: {area:g} м²\nЦена: {price_line}\n{detected}'
            f'<a href="{escape(item.url, quote=True)}">Открыть объявление</a>')


async def save_scan(deal, items, *, source='krisha', section='property', checked_at=None,
                    require_enabled=False, complete=True, photo_attempts=()):
    # Keep the original scan keys for property and OLX so deployment does not reset their baselines.
    scan_id = f'olx-{deal}' if source == 'olx' else (deal if section == 'property' else f'{section}-{deal}')
    checked_at = _utc(checked_at) if checked_at else datetime.now(UTC)
    async with scan_lock, SessionLocal() as session:
        preference = await session.get(CommercialMonitorPreference, 1)
        enabled = preference.enabled if preference is not None else get_settings().commercial_monitor_enabled
        if require_enabled and not enabled:
            return False
        tracking_started_at = _utc(preference.tracking_started_at) if preference and preference.tracking_started_at else None
        scan = await session.get(CommercialScan, scan_id)
        initialized = bool(scan and scan.initialized)
        if scan is None and complete:
            scan = CommercialScan(deal_type=scan_id, initialized=False, started_at=checked_at)
            session.add(scan)
        eligible_rows = {}
        for item in items:
            facts = eligible(item, section)
            if facts is not None:
                key = f'olx:{olx_advert_id(item.url)}' if source == 'olx' else item.url
                eligible_rows[key] = (item, *facts)
        known_rows = {}
        if eligible_rows:
            known_rows = {row.url: row for row in (await session.scalars(
                select(CommercialListing).where(CommercialListing.url.in_(eligible_rows))
            )).all()}
        for key, (item, area, price) in eligible_rows.items():
            row = known_rows.get(key)
            is_new = row is None
            gallery = krisha_gallery(item)
            photo_url = gallery[0] if gallery else public_listing_photo(item.photo_url, source)
            if row is None:
                row = CommercialListing(url=key, deal_type=deal, title=item.title, area=area,
                                        price=price, detected_at=checked_at, photo_url=photo_url, photo_urls=gallery)
                session.add(row)
            else:
                if gallery:
                    row.photo_urls = gallery
                    row.photo_url = photo_url
                elif photo_url and not row.photo_urls:
                    row.photo_url = photo_url
                old_price = krisha_primary_price(row.price) if source == 'krisha' else row.price
                if initialized and old_price and price and old_price != price:
                    session.add(CommercialEvent(url=item.url, deal_type=deal, message=event_message(
                        item, deal, area, price, old_price, section=section, detected_at=row.detected_at),
                        status='queued', photo_url=row.photo_url))
                row.title, row.area, row.seen_at = item.title, area, datetime.now(UTC)
                if price:
                    row.price = price

            if source == 'krisha' and (gallery or key in photo_attempts):
                row.photo_checked_at = _latest((row.photo_checked_at, checked_at))

            if item.published_at is not None and item.published_at.utcoffset() is not None:
                published_at = _utc(item.published_at)
                if published_at <= checked_at and (
                    row.published_at is None or published_at < _utc(row.published_at)
                ):
                    row.published_at = published_at

            if is_new and initialized:
                if tracking_started_at is not None:
                    first_discovery = tracking_started_at <= checked_at
                else:
                    started_at = _utc(scan.started_at) if scan.started_at else checked_at
                    first_discovery = row.published_at is not None and started_at <= row.published_at
                if first_discovery:
                    session.add(CommercialEvent(url=item.url, deal_type=deal, message=event_message(
                        item, deal, area, price, section=section,
                        detected_at=checked_at if tracking_started_at else None),
                        status='queued', photo_url=row.photo_url))
        if complete:
            scan.initialized = True
            scan.last_checked_at = _latest((scan.last_checked_at, checked_at))
        await session.commit()
    return True


def commercial_telegram_topics(settings) -> dict[str | None, int]:
    rent = getattr(settings, 'telegram_business_rent_thread_id', None)
    sale = getattr(settings, 'telegram_business_sale_thread_id', None)
    if rent is not None or sale is not None:
        if not rent or not sale or rent == sale:
            return {}
        return {'rent': rent, 'sale': sale}
    legacy = settings.telegram_business_thread_id
    return {None: legacy} if legacy else {}


async def _commercial_event_listing(session, event):
    olx_id = olx_advert_id(event.url)
    key = f'olx:{olx_id}' if olx_id else normalize_krisha_listing_url(event.url)
    return await session.get(CommercialListing, key) if key else None


async def _post_commercial_event(client, settings, event, *, thread_id=None):
    endpoint = f'https://api.telegram.org/bot{settings.telegram_bot_token}'
    target = {'chat_id': settings.telegram_chat_id, 'message_thread_id': thread_id or settings.telegram_business_thread_id,
              'parse_mode': 'HTML'}
    source = 'olx' if normalize_olx_listing_url(event.url) else 'krisha'
    photo = public_listing_photo(event.photo_url, source)
    sent_at = _local_time(datetime.now(UTC), telegram_timezone(settings))
    message = LEGACY_DETECTION_OFFSET.sub(r'\1', event.message)
    message = f'{message}\nОтправлено {sent_at}'
    if photo and len(message) <= 1024:
        response = await client.post(f'{endpoint}/sendPhoto', json={
            **target, 'photo': photo, 'caption': message,
        })
        if response.status_code != 400:
            return response
        error = response.json()
        description = str(error.get('description', '')).casefold()
        media_failure = any(marker in description for marker in (
            'failed to get http url content', 'wrong file identifier', 'wrong type of the web page content',
            'webpage_media_empty', 'photo_invalid_dimensions', 'image_process_failed', 'file is too big',
        ))
        # An explicit media rejection is safe to replace with one text message.
        if error.get('ok') is not False or not media_failure:
            return response
    return await client.post(f'{endpoint}/sendMessage', json={
        **target, 'text': message, 'link_preview_options': {'is_disabled': True},
    })


async def send_queued(settings, *, require_enabled=False):
    topics = commercial_telegram_topics(settings)
    if not topics:
        log.warning('Commercial Telegram routing requires two different topic IDs in split mode')
        return False
    legacy = topics.get(None)
    delivered_all = True
    async with delivery_lock, SessionLocal() as session:
        events = (await session.scalars(select(CommercialEvent).where(CommercialEvent.status == 'queued').order_by(CommercialEvent.id))).all()
        async with httpx.AsyncClient(timeout=15) as client:
            for event in events:
                if require_enabled and not await _monitor_enabled(session, settings):
                    return False
                listing = None
                if not event.photo_url or (not legacy and event.deal_type is None):
                    listing = await _commercial_event_listing(session, event)
                if legacy:
                    thread_id = legacy
                else:
                    deal_type = event.deal_type or (listing.deal_type if listing else None)
                    thread_id = topics.get(deal_type)
                    if thread_id is None:
                        log.warning('Commercial Telegram event %s is waiting for a known deal type', event.id)
                        delivered_all = False
                        continue
                    event.deal_type = deal_type
                if not event.photo_url and listing:
                    event.photo_url = listing.photo_url
                # Persist the claim before sending: an ambiguous timeout must not duplicate a message.
                event.status = 'pending'
                await session.commit()
                try:
                    response = await _post_commercial_event(client, settings, event, thread_id=thread_id)
                    result = response.json()
                    if 400 <= response.status_code < 500 and result.get('ok') is False:
                        log.warning('Telegram rejected commercial event %s (HTTP %s)', event.id, response.status_code)
                        event.status = 'queued'
                        await session.commit()
                        return False
                    response.raise_for_status()
                    if not result.get('ok'):
                        raise RuntimeError('Telegram rejected the message')
                except (httpx.HTTPError, ValueError, RuntimeError):
                    log.warning('Commercial Telegram delivery could not be confirmed (event %s)', event.id)
                    return False
                event.status = 'sent'
                event.sent_with_photo = bool((result.get('result') or {}).get('photo'))
                await session.commit()
    return delivered_all


async def send_commercial_events(settings) -> bool:
    if not (settings.telegram_enabled and settings.telegram_bot_token and settings.telegram_chat_id):
        return True
    if not (settings.telegram_business_thread_id or getattr(settings, 'telegram_business_rent_thread_id', None)
            or getattr(settings, 'telegram_business_sale_thread_id', None)):
        return True
    if not await commercial_monitor_enabled(settings):
        return False
    return await send_queued(settings, require_enabled=True)


async def run_once(settings, *, krisha_retry_at):
    if not await commercial_monitor_enabled(settings):
        return krisha_retry_at
    delivery_ready = await send_commercial_events(settings)
    for deal in OLX_ROUTES:
        if not await commercial_monitor_enabled(settings):
            return krisha_retry_at
        try:
            items = await fetch_olx_catalog(deal)
            await save_scan(deal, items, source='olx', require_enabled=True)
        except (httpx.HTTPError, RuntimeError, ValueError):
            log.warning('Commercial OLX search unavailable for %s', deal, exc_info=True)
        else:
            if delivery_ready:
                delivery_ready = await send_commercial_events(settings)

    loop = asyncio.get_running_loop()
    if loop.time() < krisha_retry_at:
        return krisha_retry_at
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'ru-KZ,ru;q=0.9'}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        for (section, deal), url in KRISHA_CATALOGS.items():
            if not await commercial_monitor_enabled(settings):
                return krisha_retry_at
            try:
                items = await observed_fetch('krisha', url, 'search', fetch_catalog, client, url, section)
                await save_scan(deal, items, section=section, require_enabled=True)
            except SourceCoolingDown:
                return loop.time() + 300
            except KrishaSearchBlocked as exc:
                log.warning('%s. Следующая проверка коммерческой выдачи через час.', exc)
                return loop.time() + KRISHA_COOLDOWN_SECONDS
            except (httpx.HTTPError, RuntimeError):
                log.warning('Commercial Krisha search unavailable for %s / %s', section, deal, exc_info=True)
            await asyncio.sleep(2)
    if delivery_ready:
        await send_commercial_events(settings)
    return krisha_retry_at


async def periodic_commercial(stop_event: asyncio.Event, monitor_changed: asyncio.Event):
    settings = get_settings()
    loop = asyncio.get_running_loop()
    krisha_retry_at = 0.0
    interval = settings.commercial_interval_minutes * 60
    while not stop_event.is_set():
        monitor_changed.clear()
        started = loop.time()
        try:
            krisha_retry_at = await run_once(settings, krisha_retry_at=krisha_retry_at)
        except Exception:
            log.exception('Commercial monitor failed')
        waiters = [asyncio.create_task(event.wait()) for event in (stop_event, monitor_changed)]
        try:
            await asyncio.wait(waiters, timeout=max(0, started + interval - loop.time()),
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
