"""Opt-in local digest and conservative import-degradation notifications."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import SessionLocal
from app.import_observability import ImportEvent
from app.models import Listing, ListingObservation, SearchResult, TelegramNotification


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _configured(settings) -> bool:
    return bool(settings.telegram_enabled and settings.telegram_bot_token and settings.telegram_chat_id)


def _zone(settings):
    try:
        return ZoneInfo(getattr(settings, 'telegram_digest_timezone', 'Asia/Almaty'))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return timezone(timedelta(hours=5)) if getattr(settings, 'telegram_digest_timezone', 'Asia/Almaty') == 'Asia/Almaty' else UTC


async def _claim_and_send(event_key: str, event_type: str, message: str, settings) -> bool:
    """Unique insert claims each event once. Explicit failures alone may retry."""
    async with SessionLocal() as session:
        session.add(TelegramNotification(event_key=event_key, event_type=event_type, listing_id=None,
                                         message=message[:3800], status='pending', attempts=1))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            result = await session.execute(update(TelegramNotification).where(
                TelegramNotification.event_key == event_key, TelegramNotification.status == 'error'
            ).values(status='pending', attempts=TelegramNotification.attempts + 1, error=None))
            await session.commit()
            if not result.rowcount:
                return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage',
                json={'chat_id':settings.telegram_chat_id, 'text':message[:3800], 'disable_web_page_preview':True})
            response.raise_for_status()
            if not response.json().get('ok'):
                raise RuntimeError('Telegram отклонил сообщение')
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        # A timeout or lost response may follow successful delivery. Keep it
        # pending to prevent duplicates; only an explicit Telegram rejection is retryable.
        confirmed_failure = isinstance(exc, RuntimeError)
        async with SessionLocal() as session:
            await session.execute(update(TelegramNotification).where(TelegramNotification.event_key == event_key)
                                  .values(status='error' if confirmed_failure else 'pending',
                                          error='Не удалось подтвердить отправку'))
            await session.commit()
        return False
    async with SessionLocal() as session:
        await session.execute(update(TelegramNotification).where(TelegramNotification.event_key == event_key)
                              .values(status='sent', sent_at=datetime.now(UTC), error=None))
        await session.commit()
    return True


async def _degradation_events(session, now: datetime, day: str) -> list[tuple[str,str,str]]:
    """Count observed errors separately from access blocks, without guessing causes."""
    events = (await session.scalars(select(ImportEvent).where(
        ImportEvent.observed_at >= now - timedelta(hours=1),
        ImportEvent.observed_at <= now, ImportEvent.method.in_(('search','auto','manual','check','import')),
        ImportEvent.status.in_(('success','blocked','error')),
    ).order_by(ImportEvent.id.desc()))).all()
    alerts = []
    for source in ('krisha','olx'):
        recent = [event for event in events if event.source == source][:20]
        # A latest success signals recovery; avoid alarming about old failures.
        if len(recent) < 3 or recent[0].status == 'success':
            continue
        for kind in ('blocked','error'):
            count = sum(event.status == kind for event in recent)
            if count < 3:
                continue
            label = 'Krisha' if source == 'krisha' else 'OLX'
            detail = ('источник ограничивает доступ' if kind == 'blocked'
                      else 'ошибки получения или обработки данных; точная причина не установлена')
            message = (f'ApartmentAgent: {label} — {detail}. За последний час: {count} '
                       f'событий из {len(recent)} проверок. Откройте журнал импорта в приложении.')
            alerts.append((f'import_alert:{source}:{kind}:{day}', 'import_alert', message))
    # Compare populated optional fields with this source's own preceding week.
    # Advertisements legitimately omit fields, so absolute absence alone is not a parser fault.
    history = (await session.scalars(select(ImportEvent).where(
        ImportEvent.observed_at >= now - timedelta(days=7), ImportEvent.observed_at < now - timedelta(hours=1),
        ImportEvent.method.in_(("auto", "manual", "check", "import", "browser")), ImportEvent.status == "success",
    ).order_by(ImportEvent.id.desc()).limit(2000))).all()
    for source in ("krisha", "olx"):
        recent_fields = (await session.scalars(select(ImportEvent).where(
            ImportEvent.source == source, ImportEvent.observed_at >= now - timedelta(hours=1),
            ImportEvent.observed_at <= now, ImportEvent.method.in_(("auto", "manual", "check", "import", "browser")),
            ImportEvent.status == "success").order_by(ImportEvent.id.desc()).limit(100))).all()
        current = [event for event in recent_fields if event.fields]
        baseline = [event for event in history if event.source == source and event.fields]
        if len(current) < 5 or len(baseline) < 10:
            continue
        labels = {"bathrooms_count":"санузлы", "repair_condition":"ремонт", "seller_type":"тип продавца"}
        degraded = []
        for field, label in labels.items():
            old_rate = sum(field in event.fields for event in baseline) / len(baseline)
            new_rate = sum(field in event.fields for event in current) / len(current)
            if old_rate >= .6 and new_rate <= .2 and old_rate - new_rate >= .5:
                degraded.append(label)
        if degraded:
            label = "Krisha" if source == "krisha" else "OLX"
            message = (f"ApartmentAgent: {label} — реже извлекаются поля: {', '.join(degraded)}. "
                       f"За час: {len(current)} карточек; сравнение с {len(baseline)} за предыдущую неделю. "
                       "Причина может быть в данных источника или извлечении. Проверьте журнал импорта.")
            alerts.append((f"import_alert:{source}:fields:{day}", "import_alert", message))
    return alerts


def _listing_relevant(row: Listing, settings) -> bool:
    from app.cities import city_matches
    from app.market_type import matches_market_type
    from app.search_agent import conditions_for_listing
    if not city_matches(getattr(settings, 'search_city', 'astana'), None, row.city): return False
    if row.rooms < getattr(settings, 'target_rooms', 2): return False
    if row.area_m2 < getattr(settings, 'min_area_m2', 0): return False
    max_area = getattr(settings, 'max_area_m2', None)
    if max_area is not None and row.area_m2 > max_area: return False
    minimum = getattr(settings, 'min_price_kzt', None)
    maximum = getattr(settings, 'max_price_kzt', None)
    if minimum is not None and row.price_kzt < minimum: return False
    if maximum is not None and row.price_kzt > maximum: return False
    if getattr(settings, 'exclude_pledged', False) and row.is_pledged is True: return False
    min_bathrooms = getattr(settings, 'min_bathrooms', 0)
    if min_bathrooms and (row.bathrooms_count is None or row.bathrooms_count < min_bathrooms): return False
    if not matches_market_type(getattr(settings, 'market_type', 'all'), row.market_type): return False
    return not conditions_for_listing(row, getattr(settings, 'search_conditions', {})).get('failed_hard')


async def _digest_message(session, now: datetime, settings) -> str:
    from app.availability import unavailable_urls
    from app.cities import city_matches
    from app.property_duplicates import canonical_url
    from app.search_analysis import analyze_search_result, is_target_search_result
    from app.seller import seller_category
    from app.workflow import hidden_urls
    since = now - timedelta(days=1)
    # Local observations only: do not infer unseen removals or market-wide change.
    found = (await session.scalars(select(SearchResult).where(
        SearchResult.first_seen >= since, SearchResult.first_seen <= now))).all()
    rows = (await session.scalars(select(Listing))).all()
    relevant = {row.id:row for row in rows if _listing_relevant(row, settings)}
    unavailable_links = await unavailable_urls(session)
    hidden = await hidden_urls(session)

    def found_matches(row):
        if not city_matches(getattr(settings, 'search_city', 'astana'), row.city): return False
        if getattr(settings, 'search_seller', 'all') == 'owner' and seller_category(row.seller_type) != 'owner':
            return False
        if getattr(settings, 'exclude_pledged', False) and row.is_pledged is True: return False
        bathrooms = getattr(settings, 'min_bathrooms', 0)
        if bathrooms and (row.bathrooms_count is None or row.bathrooms_count < bathrooms): return False
        if getattr(settings, 'market_type', 'all') != 'all' and row.market_type != settings.market_type: return False
        from app.search_agent import conditions_for_result
        if conditions_for_result(row, conditions=getattr(settings, 'search_conditions', {})).get('failed_hard'): return False
        return is_target_search_result(row.title, row.snippet, max_price=getattr(settings,'max_price_kzt',2_000_000_000),
            min_price=getattr(settings,'min_price_kzt',None), min_area=getattr(settings,'min_area_m2',0),
            max_area=getattr(settings,'max_area_m2',None), target_rooms=getattr(settings,'target_rooms',2))
    found = [row for row in found if found_matches(row)]
    cards = [row for row in relevant.values() if row.created_at and since <= _aware(row.created_at) <= now]
    events = (await session.scalars(select(ListingObservation).where(
        ListingObservation.observed_at >= since, ListingObservation.observed_at <= now))).all()
    cuts = []
    for event in events:
        if event.listing_id not in relevant: continue
        price = (event.changes or {}).get('price_kzt') or {}
        old, new = price.get('old'), price.get('new')
        if type(old) in (int,float) and type(new) in (int,float) and new < old:
            cuts.append((old-new, event.listing_id, old, new))
    cuts.sort(reverse=True)
    target_events = (await session.scalars(select(TelegramNotification).where(
        TelegramNotification.event_type == 'target_price', TelegramNotification.status == 'sent',
        TelegramNotification.sent_at >= since, TelegramNotification.sent_at <= now))).all()
    target_ids = {event.listing_id for event in target_events if event.listing_id in relevant}
    def observed_in_window(value):
        return bool(value and since <= _aware(value) <= now)
    archived = sum(observed_in_window(row.source_archived_at) for row in relevant.values())
    unavailable = sum(observed_in_window(row.source_unavailable_at) for row in relevant.values())
    sold = sum(observed_in_window(row.sold_at) for row in relevant.values())
    result = ['ApartmentAgent — обзор за последние 24 часа',
              'Текущий город и условия поиска.', f'Найдено объявлений: {len(found)}',
              f'Сохранено карточек: {len(cards)}']
    ranked = []
    for row in found:
        url = canonical_url(row.url)
        if url in unavailable_links or url in hidden:
            continue
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        if (parts.scheme != 'https' or parts.hostname not in {'krisha.kz', 'www.krisha.kz', 'olx.kz', 'www.olx.kz'}
                or parts.username or parts.password or len(url) > 350):
            continue
        score = analyze_search_result(
            row.title, row.snippet, max_price=getattr(settings, 'max_price_kzt', 2_000_000_000),
            min_area=getattr(settings, 'min_area_m2', 0), max_area=getattr(settings, 'max_area_m2', None),
            target_rooms=getattr(settings, 'target_rooms', 2),
            mortgage_supported=row.mortgage_supported, is_pledged=row.is_pledged,
        )
        price = score['price_kzt']
        ranked.append((score['score'], _aware(row.first_seen), url, price))
    seen = set()
    for _, _, url, price in sorted(ranked, reverse=True):
        if url in seen:
            continue
        seen.add(url)
        label = f"{price:,} ₸".replace(',', ' ') if price else 'цена уточняется'
        result.append(f'  {label} · {url}')
        if len(seen) == 7:
            break
    result.append(f'Наблюдений снижения цены: {len(cuts)}')
    for _, listing_id, old, new in cuts[:3]:
        result.append(f'  №{listing_id}: {old:,.0f} → {new:,.0f} ₸'.replace(',', ' '))
    result.extend([f'Достигнутая цель цены (уже отправлено уведомление): {len(target_ids)}',
                   f'Отмечено в архиве источника: {archived}', f'Стало недоступно у источника: {unavailable}',
                   f'Отмечено проданным пользователем: {sold}',
                   'Архив и недоступность не означают продажу. Данные основаны на наблюдениях приложения.'])
    return '\n'.join(result)


async def run_notifications_once(now: datetime | None = None) -> dict:
    """Check once. Both notification types require separate opt-in and Telegram setup."""
    settings = get_settings()
    result = {'configured':_configured(settings), 'alerts_sent':0, 'digest_sent':False}
    alerts_enabled = bool(getattr(settings,'telegram_parser_alerts_enabled',False))
    digest_enabled = bool(getattr(settings,'telegram_daily_digest_enabled',False))
    if not result['configured'] or not (alerts_enabled or digest_enabled):
        return result
    now = _aware(now or datetime.now(UTC))
    local = now.astimezone(_zone(settings))
    day = local.date().isoformat()
    async with SessionLocal() as session:
        alerts = await _degradation_events(session, now, day) if alerts_enabled else []
        hour = getattr(settings,'telegram_digest_hour',9)
        hour = hour if isinstance(hour,int) and not isinstance(hour,bool) and 0 <= hour <= 23 else 9
        due = digest_enabled and local.hour >= hour
        digest = await _digest_message(session, now, settings) if due else None
    for key, kind, message in alerts:
        result['alerts_sent'] += int(await _claim_and_send(key,kind,message,settings))
    if digest:
        result['digest_sent'] = await _claim_and_send(f'daily_digest:{day}', 'daily_digest', digest, settings)
    return result


async def periodic_digest(stop_event: asyncio.Event) -> None:
    import logging
    logger = logging.getLogger(__name__)
    while not stop_event.is_set():
        try:
            await run_notifications_once()
        except Exception:
            logger.exception('Local digest check failed')
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=300)
        except TimeoutError:
            pass
