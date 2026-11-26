from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service, listing_tracking
from app.models import Base, Listing, ListingLike, ListingObservation, ListingTargetPrice, SearchResult
from app.schemas import ListingCreate

@pytest_asyncio.fixture
async def sessions(monkeypatch):
    import app.listing_quality  # Register the correction table before creating the test database.
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, 'SessionLocal', factory)
    async def no_external(payload, **kwargs): return payload, False, False
    monkeypatch.setattr(listing_service, '_enrich_twogis', no_external)
    yield factory
    await engine.dispose()


def payload(**kwargs):
    return ListingCreate(**({'source':'browser', 'source_url':'https://krisha.kz/a/show/1',
        'title':'Test', 'city':'Астана', 'price_kzt':40000000, 'area_m2':100, 'rooms':4,
        'description':'Продаётся квартира с качественным хорошим ремонтом и документами.',
        'details_extracted':True} | kwargs))

@pytest.mark.asyncio
async def test_real_changes_only_and_partial_does_not_erase(sessions):
    async def save(p): return await listing_service.persist_listing(p, notify=False, refresh_ai=False)
    row = (await save(payload())).row
    await save(payload())
    await save(payload(price_kzt=39000000, description='Короткое описание', details_extracted=False))
    await save(payload(price_kzt=39000000, description='Короткое описание', details_extracted=False))
    async with sessions() as session:
        events = (await session.scalars(select(ListingObservation).where(ListingObservation.listing_id == row.id))).all()
        assert len(events) == 1
        assert events[0].changes == {'price_kzt': {'old':40000000, 'new':39000000}}

@pytest.mark.asyncio
async def test_related_age_target_matches_and_paused(sessions):
    now = datetime.now(UTC)
    async with sessions() as session:
        first = Listing(source_url='https://krisha.kz/a/show/11', title='First', city='Астана', address='Абая 10', floor=2, floors_total=5, rooms=3, area_m2=90, price_kzt=45000000, created_at=now - timedelta(days=2))
        second = Listing(source_url='https://krisha.kz/a/show/12', title='Second', city='Астана', address='Абая 10', floor=2, floors_total=5, rooms=3, area_m2=90, price_kzt=39000000, created_at=now - timedelta(days=6))
        session.add_all([first, second]); await session.flush()
        session.add(ListingTargetPrice(listing_id=first.id, target_price_kzt=40000000, include_similar=True, updated_at=now))
        session.add(SearchResult(url=first.source_url, search_engine='test', query='', title='First', first_seen=now - timedelta(days=3)))
        await session.commit()
        summary = await listing_tracking.tracking_summary(session, first.id)
        assert summary['listing_age_days'] == 3
        assert summary['related_age_days'] == 6 and summary['related_confidence'] == 'probable'
        assert summary['target']['paused'] is True
        assert summary['matching_offers'][0]['listing_id'] == second.id
        session.add(ListingLike(url=first.source_url)); await session.commit()
        assert not (await listing_tracking.tracking_summary(session, first.id))['target']['paused']
        second.source_archived_at = now; await session.commit()
        assert await listing_tracking.target_price_matches(session, first.id) == []

@pytest.fixture
def telegram_payloads(sessions, monkeypatch):
    from app import telegram
    from app.config import Settings

    monkeypatch.setattr(telegram, 'SessionLocal', sessions)
    settings = Settings(_env_file=None, telegram_enabled=True, telegram_bot_token='test',
                        telegram_chat_id='test', telegram_notify_min_score=0)
    monkeypatch.setattr(telegram, 'get_settings', lambda: settings)
    calls = []

    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {'ok':True}

    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, *, json):
            calls.append(json)
            return Response()

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 23, 20, 7, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(telegram.httpx, 'AsyncClient', FakeClient)
    monkeypatch.setattr(telegram, 'datetime', FixedDateTime)
    return calls


@pytest.mark.asyncio
async def test_listing_notification_shows_first_seen_time_and_platform(sessions, monkeypatch, telegram_payloads):
    from app.models import TelegramNotification

    async def skip_target_alerts(**kwargs):
        pass

    monkeypatch.setattr(listing_service, 'evaluate_target_alerts', skip_target_alerts)
    first_seen = datetime(2026, 11, 23, 18, 54, tzinfo=UTC)
    async with sessions() as session:
        session.add_all([
            SearchResult(url='https://www.krisha.kz/a/show/1', search_engine='test', query='',
                         title='Квартира', first_seen=first_seen),
            SearchResult(url='https://krisha.kz/a/show/1', search_engine='test', query='',
                         title='Квартира', first_seen=first_seen + timedelta(minutes=12)),
        ])
        await session.commit()

    await listing_service.persist_listing(payload(district='Есиль & Нура'), refresh_ai=False)
    await listing_service.persist_listing(payload(price_kzt=39000000, district='Есиль & Нура'), refresh_ai=False)

    assert len(telegram_payloads) == 2
    for request in telegram_payloads:
        assert request['parse_mode'] == 'HTML' and request['link_preview_options']['is_disabled']
        assert ('<blockquote>🟨 <b>Krisha.kz</b> · Астана · Есиль &amp; Нура\n'
                'Обнаружено 23.11.2026 23:54\n'
                'Отправлено 24.11.2026 01:07</blockquote>') in request['text']
    assert 'Цена изменилась' in telegram_payloads[-1]['text']
    async with sessions() as session:
        messages = await session.scalars(select(TelegramNotification.message).order_by(TelegramNotification.id))
        assert messages.all() == [request['text'] for request in telegram_payloads]


@pytest.mark.asyncio
async def test_target_notification_deduplicates_without_real_network(sessions, telegram_payloads):
    from app import telegram
    from app.models import TelegramNotification

    url = 'https://www.olx.kz/d/obyavlenie/astana-IDowt.html'
    async with sessions() as session:
        row = Listing(source_url=url, title='Test', city='Астана', rooms=2, area_m2=80, price_kzt=20,
                      created_at=datetime(2026, 11, 23, 19, tzinfo=UTC))
        session.add(row); await session.commit(); listing_id = row.id
    args = dict(listing_id=listing_id, offer_id=listing_id, price_kzt=20, target_kzt=30,
                target_updated_at=datetime.now(UTC), source_url=url, relationship='self')
    assert await telegram.notify_target_price(**args)
    assert not await telegram.notify_target_price(**args)
    assert len(telegram_payloads) == 1
    message = telegram_payloads[0]['text']
    assert ('<blockquote>🟩 <b>OLX.kz</b>\n'
            'Сохранено 24.11.2026 00:00\n'
            'Отправлено 24.11.2026 01:07</blockquote>') in message
    assert f'href="{url}"' in message
    async with sessions() as session:
        records = (await session.scalars(select(TelegramNotification))).all()
        assert len(records) == 1 and records[0].status == 'sent'


@pytest.mark.asyncio
async def test_listing_notification_sends_photo_with_ordered_fields(sessions, monkeypatch, telegram_payloads):
    async def skip_target_alerts(**kwargs):
        pass

    monkeypatch.setattr(listing_service, 'evaluate_target_alerts', skip_target_alerts)
    photo = 'https://alaps-photos-kr.kcdn.kz/a/photo.jpg'
    await listing_service.persist_listing(payload(photo_urls=[photo]), refresh_ai=False)
    sent = telegram_payloads[0]
    assert sent['photo'] == photo
    caption = sent['caption']
    assert caption.index('Цена:') < caption.index('Krisha.kz') < caption.index('Сохранено') < caption.index('Отправлено')
    assert '(UTC' not in caption
    assert 'https://krisha.kz/a/show/1' in caption


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['photo', 'rejected_photo', 'no_photo', 'other_error'])
async def test_apartment_photo_send_and_explicit_fallback(mode):
    import httpx
    from types import SimpleNamespace
    from app import telegram

    calls = []
    class Client:
        async def post(self, url, *, json):
            calls.append((url.rsplit('/', 1)[-1], json))
            if url.endswith('/sendPhoto') and mode in {'rejected_photo', 'other_error'}:
                reason = ('Bad Request: failed to get HTTP URL content' if mode == 'rejected_photo'
                          else 'Bad Request: chat not found')
                return httpx.Response(400, json={'ok': False, 'description': reason})
            return httpx.Response(200, json={'ok': True})

    settings = SimpleNamespace(telegram_bot_token='test', telegram_chat_id='-1001', telegram_apartment_thread_id=42)
    photo = 'https://alaps-photos-kr.kcdn.kz/a/photo.jpg'
    message = 'Цена: 40 000 000 ₸\nИсточник: Krisha'
    response = await telegram._send_apartment_message(Client(), settings, message, None if mode == 'no_photo' else photo)
    assert response.status_code == (400 if mode == 'other_error' else 200)
    assert [method for method, _ in calls] == {
        'photo': ['sendPhoto'], 'rejected_photo': ['sendPhoto', 'sendMessage'],
        'no_photo': ['sendMessage'], 'other_error': ['sendPhoto'],
    }[mode]
    assert all(data['message_thread_id'] == 42 for _, data in calls)
    if mode in {'no_photo', 'rejected_photo'}:
        assert calls[-1][1]['text'] == message
        assert calls[-1][1]['link_preview_options']['is_disabled'] is True
    if mode != 'no_photo':
        assert calls[0][1]['photo'] == photo and calls[0][1]['caption'] == message


def test_apartment_photo_uses_source_specific_hosts():
    from app.telegram import _listing_photo
    assert _listing_photo('https://krisha.kz/a/show/123', [
        'https://example.com/photo.jpg', 'https://alaps-photos-kr.kcdn.kz/a/photo.jpg',
    ]) == 'https://alaps-photos-kr.kcdn.kz/a/photo.jpg'
    assert _listing_photo('https://astana.etagi.com/realty/123/', [
        'https://cdn.esoft.digital/content/photo.jpg',
    ]) == 'https://cdn.esoft.digital/content/photo.jpg'
    assert _listing_photo('https://krisha.kz/a/show/123', ['https://cdn.esoft.digital/content/photo.jpg']) is None
