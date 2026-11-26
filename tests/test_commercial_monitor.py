import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import httpx
import pytest

from app import commercial
from app.telegram import apartment_topic
from app.krisha_search import KrishaSearchItem, parse_krisha_search_page
from app.models import Base, CommercialEvent, CommercialListing


def item(url, area, price):
    return KrishaSearchItem(title=f'Офис · {area} м²', url=url, snippet=price)


def test_minimum_area_and_rent_period():
    assert commercial.eligible(item('https://krisha.kz/a/show/1', '99,9', '1 ₸')) is None
    assert commercial.eligible(item('https://krisha.kz/a/show/2', '100', '500 000 ₸ за месяц')) == (100, '500 000 ₸ за месяц')


def test_apartment_topic_is_optional():
    assert apartment_topic(SimpleNamespace(telegram_apartment_thread_id=42)) == {'message_thread_id': 42}
    assert apartment_topic(SimpleNamespace()) == {}


async def test_blocked_catalog_is_not_treated_as_empty():
    async def handler(request):
        return httpx.Response(468, request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match='468'):
            await commercial.fetch_catalog(client, commercial.KRISHA_CATALOGS['property', 'rent'])


def test_krisha_categories_and_industrial_building_area():
    assert set(commercial.KRISHA_CATALOGS) == {
        ('property', 'rent'), ('property', 'sale'), ('business', 'sale'),
        ('industry', 'rent'), ('industry', 'sale'),
    }
    html = '''
    <section class="a-search-list">
      <div class="a-card" data-id="1">
        <div class="a-card__main-info">
          <a class="a-card__title" href="/a/show/1">Промбаза · 25 соток</a>
          <div class="a-card__price">60 000 000 ₸</div>
        </div>
        <div class="a-card__text">Участок 25 соток, помещение 186 кв. м.</div>
      </div>
      <div class="a-card" data-id="2">
        <div class="a-card__main-info">
          <a class="a-card__title" href="/a/show/2">Промбаза · 500 м²</a>
          <div class="a-card__price">20 000 000 ₸</div>
        </div>
        <div class="a-card__text">Площадь участка 500 м², здание 90 м².</div>
      </div>
    </section>
    <script type="application/ld+json">
      {"url":"/a/show/1","dateCreated":"2026-11-25T10:30:00+05:00"}
    </script>
    '''
    first, second = parse_krisha_search_page(html, expected_city='ekibastuz')
    assert commercial.eligible(first, 'industry') == (186, '60 000 000 ₸')
    assert commercial.eligible(second, 'industry') is None
    assert first.published_at == datetime(2026, 11, 25, 5, 30, tzinfo=UTC)


def test_industrial_catalog_uses_building_area_with_compact_units():
    ad = KrishaSearchItem("Промбаза · 0.1136 га", "https://krisha.kz/a/show/1013488222",
                         "1 800 000 ₸ за месяц", description="Нежилое помещение 596, 5 квм")
    assert commercial.eligible(ad, "industry") == (596.5, "1 800 000 ₸ за месяц")


def test_public_krisha_catalog_photo_belongs_to_its_listing():
    photo = "https://krisha-photos.kcdn.online/webp/c3/c3290962-7a14-489a-8e48-15b214009973/1-120x90.jpg"
    html = f"""
    <section class="a-search-list">
      <div class="a-card" data-id="1012376627">
        <img src="/static/frontend/images/empty-photo-750.png" data-src="{photo}">
        <div class="a-card__main-info">
          <a class="a-card__title" href="/a/show/1012376627">Бокс · 100 м²</a>
        </div>
      </div>
      <div class="a-card" data-id="2">
        <img src="https://example.test/logo.jpg">
        <div class="a-card__main-info">
          <a class="a-card__title" href="/a/show/2">Склад · 120 м²</a>
        </div>
      </div>
    </section>
    """
    assert [ad.photo_url for ad in parse_krisha_search_page(html, expected_city="ekibastuz")] == [photo, None]


async def test_new_krisha_business_uses_its_own_baseline(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(commercial, 'SessionLocal', async_sessionmaker(engine, expire_on_commit=False))
    start = datetime(2026, 11, 25, 10, tzinfo=UTC)
    await commercial.save_scan('sale', [], checked_at=start)
    existing = item('https://krisha.kz/a/show/1', '120', '15 000 000 ₸')
    existing.published_at = start + timedelta(minutes=1)
    await commercial.save_scan('sale', [existing], section='business', checked_at=start + timedelta(minutes=2))
    new = item('https://krisha.kz/a/show/2', '120', '25 000 000 ₸')
    new.published_at = start + timedelta(minutes=3)
    await commercial.save_scan('sale', [existing, new], section='business', checked_at=start + timedelta(minutes=4))
    async with commercial.SessionLocal() as session:
        events = (await session.scalars(select(CommercialEvent))).all()
        assert [event.url for event in events] == [new.url]
        assert 'Готовый бизнес · Продажа' in events[0].message
    await engine.dispose()


async def test_first_scan_baselines_and_undated_krisha_listing_is_not_reported_as_new(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(commercial, 'SessionLocal', async_sessionmaker(engine, expire_on_commit=False))
    first = item('https://krisha.kz/a/show/1', '120', '500 000 ₸ за месяц')
    await commercial.save_scan('rent', [first])
    async with commercial.SessionLocal() as session:
        assert (await session.scalars(select(CommercialEvent))).all() == []
    changed = item(first.url, '120', '450 000 ₸ за месяц')
    new = item('https://krisha.kz/a/show/2', '100', '700 000 ₸ за месяц')
    await commercial.save_scan('rent', [changed, new])
    await commercial.save_scan('rent', [changed, new])
    async with commercial.SessionLocal() as session:
        events = (await session.scalars(select(CommercialEvent).order_by(CommercialEvent.id))).all()
        assert len(events) == 1
        assert '500 000 ₸ за месяц → 450 000 ₸ за месяц' in events[0].message
    await engine.dispose()


async def test_krisha_price_per_square_metre_does_not_trigger_a_price_change(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(commercial, 'SessionLocal', async_sessionmaker(engine, expire_on_commit=False))
    ad = item('https://krisha.kz/a/show/1014943704', '130', '42 000 000 ₸ за всё')
    await commercial.save_scan('sale', [ad])
    with_unit_price = item(ad.url, '130', '42 000 000 ₸ за всё 323 077 ₸ за м²')
    await commercial.save_scan('sale', [with_unit_price])
    async with commercial.SessionLocal() as session:
        assert (await session.get(CommercialListing, ad.url)).price == ad.snippet
        assert (await session.scalars(select(CommercialEvent))).all() == []
        # Older monitoring runs may still have the combined value stored.
        (await session.get(CommercialListing, ad.url)).price = with_unit_price.snippet
        await session.commit()
    await commercial.save_scan('sale', [ad])
    genuinely_changed = item(ad.url, '130', '41 000 000 ₸ за всё 315 385 ₸ за м²')
    await commercial.save_scan('sale', [genuinely_changed])
    async with commercial.SessionLocal() as session:
        events = (await session.scalars(select(CommercialEvent))).all()
        assert len(events) == 1
        assert '42 000 000 ₸ за всё → 41 000 000 ₸ за всё' in events[0].message
    await engine.dispose()


async def test_olx_promotion_does_not_make_old_listing_new(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(commercial, 'SessionLocal', async_sessionmaker(engine, expire_on_commit=False))
    start = datetime(2026, 11, 24, 10, tzinfo=UTC)
    await commercial.save_scan('sale', [], source='olx', checked_at=start)
    old = commercial.OlxCommercialItem('Склад', 'https://www.olx.kz/d/obyavlenie/sklad-IDabc.html',
                                       '10 000 000 ₸', 130, start - timedelta(days=1))
    fresh = commercial.OlxCommercialItem('Офис', 'https://www.olx.kz/d/obyavlenie/ofis-IDdef.html',
                                         '18 000 000 ₸', 100, start + timedelta(minutes=1))
    await commercial.save_scan('sale', [old, fresh], source='olx', checked_at=start + timedelta(minutes=2))
    async with commercial.SessionLocal() as session:
        events = (await session.scalars(select(CommercialEvent))).all()
        assert [event.url for event in events] == [fresh.url]
    await engine.dispose()


async def test_business_notification_retries_rejection_in_same_topic(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(commercial, 'SessionLocal', async_sessionmaker(engine, expire_on_commit=False))
    async with commercial.SessionLocal() as session:
        session.add(CommercialEvent(url='https://www.olx.kz/d/obyavlenie/ofis-IDdef.html', message='Price changed'))
        await session.commit()
    payloads = []

    async def receive(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(403, json={'ok': False})
        return httpx.Response(200, json={'ok': True})

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(receive)
    monkeypatch.setattr(commercial.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    settings = SimpleNamespace(telegram_bot_token='test', telegram_chat_id='-1001', telegram_business_thread_id=5)
    assert await commercial.send_queued(settings) is False
    assert await commercial.send_queued(settings) is True
    assert await commercial.send_queued(settings) is True
    assert [payload['message_thread_id'] for payload in payloads] == [5, 5]
    async with commercial.SessionLocal() as session:
        assert await session.scalar(select(CommercialEvent.status)) == 'sent'
    await engine.dispose()


@pytest.fixture
async def commercial_delivery(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(commercial, 'SessionLocal', sessions)
    payloads = []

    async def receive(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        result = {
            'message_id': len(payloads), 'message_thread_id': payload['message_thread_id'], 'is_topic_message': True,
        }
        if request.url.path.endswith('/sendPhoto'):
            result['photo'] = [{'file_id': 'test-photo'}]
        return httpx.Response(200, json={'ok': True, 'result': result})

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(receive)
    monkeypatch.setattr(commercial.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    settings = SimpleNamespace(
        telegram_enabled=True, telegram_bot_token='test', telegram_chat_id='-1001',
        telegram_business_thread_id=None, telegram_business_rent_thread_id=51, telegram_business_sale_thread_id=52,
        commercial_monitor_enabled=True,
    )
    monkeypatch.setattr(commercial, 'get_settings', lambda: settings)
    try:
        yield sessions, settings, payloads
    finally:
        await engine.dispose()


@pytest.mark.parametrize('source', ['krisha', 'olx'])
async def test_commercial_split_routes_mixed_new_ads_and_price_changes(commercial_delivery, source):
    sessions, settings, payloads = commercial_delivery
    started_at = datetime(2026, 11, 25, 10, tzinfo=UTC)
    published_at = started_at + timedelta(minutes=1)

    def ad(number, price):
        if source == 'olx':
            return commercial.OlxCommercialItem('Склад', f'https://www.olx.kz/d/obyavlenie/sklad-IDa{number}.html',
                                                price, 120, published_at)
        result = item(f'https://krisha.kz/a/show/{number}', '120', price)
        result.published_at = published_at
        return result

    old_rent = ad(1, '500 000 ₸')
    new_rent = ad(2, '700 000 ₸')
    old_sale = ad(3, '30 000 000 ₸')
    new_sale = ad(4, '40 000 000 ₸')
    await commercial.save_scan('rent', [old_rent], source=source, checked_at=started_at)
    await commercial.save_scan('sale', [old_sale], source=source, checked_at=started_at)
    await commercial.save_scan('rent', [new_rent], source=source, checked_at=started_at + timedelta(minutes=2))
    await commercial.save_scan('sale', [new_sale], source=source, checked_at=started_at + timedelta(minutes=2))
    await commercial.save_scan('sale', [ad(3, '27 000 000 ₸')], source=source, checked_at=started_at + timedelta(minutes=3))
    await commercial.save_scan('rent', [ad(1, '450 000 ₸')], source=source, checked_at=started_at + timedelta(minutes=3))

    assert await commercial.send_commercial_events(settings) is True
    assert await commercial.send_commercial_events(settings) is True
    assert [payload['message_thread_id'] for payload in payloads] == [51, 52, 52, 51]
    for payload, (listing, notice) in zip(payloads, [
        (new_rent, 'Впервые найдено'), (new_sale, 'Впервые найдено'),
        (old_sale, '30 000 000 ₸ → 27 000 000 ₸'), (old_rent, '500 000 ₸ → 450 000 ₸'),
    ], strict=True):
        assert notice in payload['text'] and listing.url in payload['text']
        assert '\nОтправлено ' in payload['text']
        assert '(UTC' not in payload['text']
        assert payload['link_preview_options'] == {'is_disabled': True}
    assert all('\nОбнаружено 25.11.2026 15:00\n' in payload['text'] for payload in payloads[2:])
    async with sessions() as session:
        events = (await session.scalars(select(CommercialEvent).order_by(CommercialEvent.id))).all()
        assert [(event.deal_type, event.status) for event in events] == [
            ('rent', 'sent'), ('sale', 'sent'), ('sale', 'sent'), ('rent', 'sent'),
        ]


@pytest.mark.parametrize(('sale_thread', 'deal'), [(None, 'rent'), (51, 'rent'), (52, None)])
async def test_commercial_split_keeps_event_queued_when_route_is_unavailable(commercial_delivery, sale_thread, deal):
    sessions, settings, payloads = commercial_delivery
    settings.telegram_business_sale_thread_id = sale_thread
    settings.telegram_business_thread_id = 5
    async with sessions.begin() as session:
        session.add(CommercialEvent(url='https://krisha.kz/a/show/99', message='Объявление', deal_type=deal))

    await commercial.send_queued(settings)

    assert payloads == []
    async with sessions() as session:
        assert await session.scalar(select(CommercialEvent.status)) == 'queued'


async def test_commercial_split_recovers_known_legacy_deals_after_an_unknown_event(commercial_delivery):
    sessions, settings, payloads = commercial_delivery
    rental_url = 'https://krisha.kz/a/show/1017318293'
    sale_url = 'https://www.olx.kz/d/obyavlenie/sklad-IDr5GNQ.html'
    async with sessions.begin() as session:
        session.add_all([
            CommercialListing(url=rental_url, deal_type='rent', title='Склад', area=120),
            CommercialListing(url='olx:IDr5GNQ', deal_type='sale', title='Офис', area=130),
            CommercialEvent(url='https://krisha.kz/a/show/99', message='Объявление без типа сделки'),
            CommercialEvent(url=rental_url, message='Аренда'),
            CommercialEvent(url=sale_url, message='Продажа'),
        ])

    await commercial.send_queued(settings)

    assert [(payload['message_thread_id'], payload['text'].splitlines()[0]) for payload in payloads] == [
        (51, 'Аренда'), (52, 'Продажа'),
    ]
    async with sessions() as session:
        events = (await session.scalars(select(CommercialEvent).order_by(CommercialEvent.id))).all()
        assert [(event.deal_type, event.status) for event in events] == [(None, 'queued'), ('rent', 'sent'), ('sale', 'sent')]


@pytest.mark.parametrize(('url', 'listing_key', 'cover'), [
    ('https://krisha.kz/a/show/1017318293?source=test', 'https://krisha.kz/a/show/1017318293',
     'https://kzala2-photos-kr.kcdn.kz/webp/82/8287575c-025b-41e9-88da-51970eb69f79/1-full.jpg'),
    ('https://www.olx.kz/d/obyavlenie/sklad-IDr5GNQ.html', 'olx:IDr5GNQ',
     'https://frankfurt.apollo.olxcdn.com:443/v1/files/test/image;s=1000x750'),
])
async def test_queued_notice_gets_cover_saved_after_the_event(commercial_delivery, url, listing_key, cover):
    sessions, settings, payloads = commercial_delivery
    async with sessions.begin() as session:
        session.add(CommercialEvent(
            url=url, deal_type='rent', message='Коммерческая аренда\nОбнаружено 25.11.2026 15:00 (UTC+05:00)',
        ))
    async with sessions.begin() as session:
        session.add(CommercialListing(url=listing_key, deal_type='rent', title='Склад', area=120, photo_url=cover))

    assert await commercial.send_commercial_events(settings) is True
    assert len(payloads) == 1
    assert payloads[0]['photo'] == cover and payloads[0]['message_thread_id'] == 51
    caption = payloads[0]['caption']
    assert '\nОбнаружено 25.11.2026 15:00\nОтправлено ' in caption
    assert '(UTC' not in caption
    async with sessions() as session:
        event = await session.scalar(select(CommercialEvent))
        assert event.photo_url == cover and event.sent_with_photo is True


@pytest.mark.parametrize(('source', 'label'), [('krisha', '🟨 <b>Krisha.kz</b>'), ('olx', '🟩 <b>OLX.kz</b>')])
def test_commercial_format_preserves_queued_event_and_orders_fields(source, label):
    old = (
        '🏢 <b>Коммерческая недвижимость · Продажа</b>\n'
        f'Цена изменилась · {"Krisha" if source == "krisha" else "OLX"} · Экибастуз\n'
        'Магазин &amp; склад\nПлощадь: 120 м²\nЦена: 42 000 000 ₸ → 40 000 000 ₸\n'
        'Обнаружено 25.11.2026 15:00 (UTC+05:00)\n'
        '<a href="https://example.com/offer">Открыть объявление</a>'
    )
    rendered = commercial._commercial_message(old, '26.11.2026 12:01')
    assert rendered.index('Цена:') < rendered.index(label) < rendered.index('Обнаружено') < rendered.index('Отправлено')
    assert 'Цена изменилась' in rendered
    assert '42 000 000 ₸ → 40 000 000 ₸' in rendered
    assert 'Магазин &amp; склад' in rendered
    assert '<a href="https://example.com/offer">Открыть объявление</a>' in rendered
    assert '(UTC' not in rendered


@pytest.mark.parametrize("reject_media", [False, True])
@pytest.mark.parametrize('split', [False, True])
async def test_listing_photo_and_caption_are_sent_together_with_safe_text_fallback(monkeypatch, reject_media, split):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(commercial, 'SessionLocal', async_sessionmaker(engine, expire_on_commit=False))
    checked_at = datetime.now(UTC)
    await commercial.save_scan('sale', [], source='olx', checked_at=checked_at)
    await commercial.start_commercial_tracking()
    photo = "https://frankfurt.apollo.olxcdn.com:443/v1/files/tc9qx86vn2xx1-KZ/image;s=1000x750"
    ad = commercial.OlxCommercialItem("Офис", "https://www.olx.kz/d/obyavlenie/office-IDoyum2.html",
                                     "42 000 000 ₸", 130, None, photo)
    await commercial.save_scan('sale', [ad], source='olx')
    calls = []

    async def receive(request):
        method = request.url.path.rsplit('/', 1)[-1]
        calls.append((method, json.loads(request.content)))
        if method == 'sendPhoto' and reject_media:
            return httpx.Response(400, json={'ok': False, 'description': 'Bad Request: failed to get HTTP URL content'})
        message = {'photo': [{'file_id': 'fake-photo'}]} if method == 'sendPhoto' else {}
        return httpx.Response(200, json={'ok': True, 'result': message})

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(receive)
    monkeypatch.setattr(commercial.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=transport, **kwargs))
    settings = SimpleNamespace(
        telegram_bot_token='test', telegram_chat_id='-1001', telegram_business_thread_id=5,
        telegram_business_rent_thread_id=51 if split else None, telegram_business_sale_thread_id=52 if split else None,
    )
    assert await commercial.send_queued(settings) is True
    assert await commercial.send_queued(settings) is True
    assert [method for method, _ in calls] == (['sendPhoto', 'sendMessage'] if reject_media else ['sendPhoto'])
    photo_payload = calls[0][1]
    assert photo_payload["photo"] == photo
    assert all(payload['message_thread_id'] == (52 if split else 5) for _, payload in calls)
    assert "42 000 000 ₸" in photo_payload["caption"] and ad.url in photo_payload["caption"]
    assert '\nОбнаружено ' in photo_payload['caption'] and '\nОтправлено ' in photo_payload['caption']
    if reject_media:
        assert calls[1][1]["text"] == photo_payload["caption"]
        assert calls[1][1]['link_preview_options'] == {'is_disabled': True}
    async with commercial.SessionLocal() as session:
        listing = await session.get(CommercialListing, "olx:IDoyum2")
        assert listing.photo_url == photo
        event = await session.scalar(select(CommercialEvent))
        assert event.status == 'sent' and event.sent_with_photo is (not reject_media)
    await engine.dispose()
