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
