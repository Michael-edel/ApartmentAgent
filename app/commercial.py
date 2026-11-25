"""Independent commercial-property watcher; first successful scan is a baseline."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import SessionLocal
from app.import_observability import SourceCoolingDown, observed_fetch
from app.krisha_search import KrishaSearchBlocked, _security_text, parse_krisha_search_page
from app.models import CommercialEvent, CommercialListing, CommercialMonitorPreference, CommercialScan
from app.olx import _get_olx, _number, _state, normalize_olx_listing_url, olx_advert_id

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


async def commercial_monitor_status(settings) -> dict:
    async with SessionLocal() as session:
        enabled = await _monitor_enabled(session, settings)
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
    }


@dataclass(slots=True)
class OlxCommercialItem:
    title: str
    url: str
    snippet: str | None
    area: float
    published_at: datetime | None


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
        items[olx_advert_id(url)] = OlxCommercialItem(title[:300], url, price, area, published_at)
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


def event_message(item, deal, area, price, old_price=None, *, section='property'):
    kind = 'Аренда' if deal == 'rent' else 'Продажа'
    header = 'Цена изменилась' if old_price else 'Новое объявление'
    price_line = f'{escape(old_price)} → {escape(price)}' if old_price and price else escape(price or 'не указана')
    source = 'OLX' if isinstance(item, OlxCommercialItem) else 'Krisha'
    return (f'🏢 <b>{SECTION_LABELS[section]} · {kind}</b>\n{header} · {source} · Экибастуз\n{escape(item.title)}\n'
            f'Площадь: {area:g} м²\nЦена: {price_line}\n'
            f'<a href="{escape(item.url, quote=True)}">Открыть объявление</a>')


async def save_scan(deal, items, *, source='krisha', section='property', checked_at=None,
                    require_enabled=False, complete=True):
    # Keep the original scan keys for property and OLX so deployment does not reset their baselines.
    scan_id = f'olx-{deal}' if source == 'olx' else (deal if section == 'property' else f'{section}-{deal}')
    checked_at = checked_at or datetime.now(UTC)
    async with scan_lock, SessionLocal() as session:
        if require_enabled and not await _monitor_enabled(session, get_settings()):
            return False
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
            old = known_rows.get(key)
            if old is None:
                session.add(CommercialListing(url=key, deal_type=deal, title=item.title, area=area, price=price))
                if initialized and item.published_at is not None:
                    started_at = scan.started_at or checked_at
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=UTC)
                    if started_at <= item.published_at <= checked_at:
                        session.add(CommercialEvent(url=item.url, message=event_message(
                            item, deal, area, price, section=section), status='queued'))
            else:
                old_price = krisha_primary_price(old.price) if source == 'krisha' else old.price
                if initialized and old_price and price and old_price != price:
                    session.add(CommercialEvent(url=item.url, message=event_message(
                        item, deal, area, price, old_price, section=section), status='queued'))
                old.title, old.area, old.seen_at = item.title, area, datetime.now(UTC)
                if price:
                    old.price = price
        if complete:
            scan.initialized = True
            scan.last_checked_at = _latest((scan.last_checked_at, checked_at))
        await session.commit()
    return True


async def send_queued(settings, *, require_enabled=False):
    async with delivery_lock, SessionLocal() as session:
        events = (await session.scalars(select(CommercialEvent).where(CommercialEvent.status == 'queued').order_by(CommercialEvent.id))).all()
        async with httpx.AsyncClient(timeout=15) as client:
            for event in events:
                if require_enabled and not await _monitor_enabled(session, settings):
                    return False
                # Persist the claim before sending: an ambiguous timeout must not duplicate a message.
                event.status = 'pending'
                await session.commit()
                try:
                    response = await client.post(f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage', json={
                        'chat_id': settings.telegram_chat_id, 'message_thread_id': settings.telegram_business_thread_id,
                        'text': event.message, 'parse_mode': 'HTML', 'disable_web_page_preview': False})
                    if 400 <= response.status_code < 500 and response.json().get('ok') is False:
                        log.warning('Telegram rejected commercial event %s (HTTP %s)', event.id, response.status_code)
                        event.status = 'queued'
                        await session.commit()
                        return False
                    response.raise_for_status()
                    if not response.json().get('ok'):
                        raise RuntimeError('Telegram rejected the message')
                except (httpx.HTTPError, ValueError, RuntimeError):
                    log.warning('Commercial Telegram delivery could not be confirmed (event %s)', event.id)
                    return False
                event.status = 'sent'
                await session.commit()
    return True


async def send_commercial_events(settings) -> bool:
    if not (settings.telegram_enabled and settings.telegram_bot_token
            and settings.telegram_chat_id and settings.telegram_business_thread_id):
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
