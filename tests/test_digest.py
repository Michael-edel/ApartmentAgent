from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app import digest
from app.import_observability import ImportEvent
from app.models import Base, Listing, ListingObservation, SearchResult, TelegramNotification


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(digest,'SessionLocal',sessions)
    settings = SimpleNamespace(telegram_enabled=True, telegram_bot_token='dummy', telegram_chat_id='dummy',
        telegram_parser_alerts_enabled=False, telegram_daily_digest_enabled=False, telegram_digest_hour=9,
        telegram_digest_timezone='Asia/Almaty')
    monkeypatch.setattr(digest,'get_settings',lambda:settings)
    calls=[]
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'ok':True}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self,url,*,json): calls.append(json); return Response()
    monkeypatch.setattr(digest.httpx,'AsyncClient',Client)
    yield SimpleNamespace(sessions=sessions, settings=settings, calls=calls)
    await engine.dispose()


@pytest.mark.asyncio
async def test_opt_in_dedupe_and_distinct_block_vs_error(env):
    now = datetime(2026,11,13,8,tzinfo=UTC)
    async with env.sessions() as session:
        for i in range(3):
            session.add(ImportEvent(source='krisha',url='',method='search',status='blocked',fields=[],http_status=468,
                                    observed_at=now-timedelta(minutes=i)))
            session.add(ImportEvent(source='olx',url='',method='auto',status='error',fields=[],
                                    observed_at=now-timedelta(minutes=i)))
        await session.commit()
    assert not (await digest.run_notifications_once(now))['alerts_sent'] and not env.calls
    env.settings.telegram_parser_alerts_enabled=True
    first = await digest.run_notifications_once(now)
    assert first['alerts_sent'] == 2 and len(env.calls) == 2
    assert any('ограничивает доступ' in call['text'] for call in env.calls)
    assert any('причина не установлена' in call['text'] for call in env.calls)
    assert (await digest.run_notifications_once(now))['alerts_sent'] == 0
    assert len(env.calls) == 2
    async with env.sessions() as session:
        records = (await session.scalars(select(TelegramNotification))).all()
        assert len(records) == 2 and all(record.status == 'sent' for record in records)


@pytest.mark.asyncio
async def test_digest_once_local_day_and_from_local_observations(env):
    env.settings.telegram_daily_digest_enabled = True
    now = datetime(2026,11,13,5,tzinfo=UTC)
    async with env.sessions() as session:
        listing = Listing(source_url='https://krisha.kz/a/show/1',title='test',city='Астана',rooms=3,
                          area_m2=80,price_kzt=30_000_000,created_at=now-timedelta(hours=1))
        session.add(listing); await session.flush()
        session.add(SearchResult(url=listing.source_url,search_engine='test',query='',title='3-комнатная квартира 80 м² 30000000 ₸',city='astana',first_seen=now-timedelta(hours=1)))
        session.add(ListingObservation(listing_id=listing.id, observed_at=now-timedelta(minutes=5),
                       changes={'price_kzt':{'old':31_000_000,'new':30_000_000}}))
        await session.commit()
    assert (await digest.run_notifications_once(now))['digest_sent']
    assert 'Найдено объявлений: 1' in env.calls[0]['text']
    assert '30 000 000 ₸ · https://krisha.kz/a/show/1' in env.calls[0]['text']
    assert 'Наблюдений снижения цены: 1' in env.calls[0]['text']
    assert not (await digest.run_notifications_once(now))['digest_sent']
    assert len(env.calls)==1

@pytest.mark.asyncio
async def test_field_alert_requires_historical_baseline(env):
    now = datetime(2026,11,13,8,tzinfo=UTC)
    env.settings.telegram_parser_alerts_enabled=True
    async with env.sessions() as session:
        for index in range(5):
            session.add(ImportEvent(source='krisha',url='',method='browser',status='success',fields=['title'],observed_at=now-timedelta(minutes=index)))
        await session.commit()
    assert (await digest.run_notifications_once(now))['alerts_sent'] == 0
    async with env.sessions() as session:
        for index in range(10):
            session.add(ImportEvent(source='krisha',url='',method='browser',status='success',fields=['title','seller_type'],observed_at=now-timedelta(days=1,minutes=index)))
        await session.commit()
    result = await digest.run_notifications_once(now)
    assert result['alerts_sent'] == 1
    assert 'тип продавца' in env.calls[0]['text']
    assert 'может быть' in env.calls[0]['text']

@pytest.mark.asyncio
async def test_digest_filters_city_and_criteria_and_separates_statuses(env):
    env.settings.telegram_daily_digest_enabled=True
    env.settings.search_city='astana'; env.settings.target_rooms=3
    env.settings.min_area_m2=70; env.settings.max_area_m2=130; env.settings.max_price_kzt=50_000_000
    now=datetime(2026,11,13,5,tzinfo=UTC)
    async with env.sessions() as session:
        rows = [Listing(source_url=f'https://krisha.kz/a/show/{idx}', title='test', city=city,rooms=rooms,
                        area_m2=90,price_kzt=price,created_at=now-timedelta(hours=2)) for idx,city,rooms,price in
                [(1,'Астана',3,40_000_000),(2,'Астана',3,38_000_000),(3,'Астана',3,36_000_000),
                 (4,'Экибастуз',3,30_000_000),(5,'Астана',2,30_000_000)]]
        session.add_all(rows); await session.flush()
        rows[0].source_archived_at=now-timedelta(minutes=1)
        rows[1].source_unavailable_at=now-timedelta(minutes=1)
        rows[2].sold_at=now-timedelta(minutes=1)
        for row,amount in [(rows[0],1_000_000),(rows[1],5_000_000),(rows[3],10_000_000)]:
            session.add(ListingObservation(listing_id=row.id,observed_at=now-timedelta(minutes=5),
                        changes={'price_kzt':{'old':row.price_kzt+amount,'new':row.price_kzt}}))
        session.add(TelegramNotification(event_key='target-test',event_type='target_price',listing_id=rows[0].id,
                    message='test',status='sent',attempts=1,sent_at=now-timedelta(minutes=5)))
        await session.commit()
    assert (await digest.run_notifications_once(now))['digest_sent']
    text=env.calls[0]['text']
    assert 'Сохранено карточек: 3' in text
    assert 'Наблюдений снижения цены: 2' in text
    assert text.index('43 000 000 → 38 000 000') < text.index('41 000 000 → 40 000 000')
    assert 'Достигнутая цель цены (уже отправлено уведомление): 1' in text
    assert 'Отмечено в архиве источника: 1' in text
    assert 'Стало недоступно у источника: 1' in text
    assert 'Отмечено проданным пользователем: 1' in text
