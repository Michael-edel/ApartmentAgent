import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service
from app.config import Settings
from app.importer import _mortgage_fields, import_krisha_listing
from app.models import Base, SearchResult
from app.mortgage import parse_mortgage_text
from app.schemas import ListingCreate
from app.scoring import assess_listing
from app.search_analysis import analyze_search_result


@pytest.mark.parametrize("text", ["Ипотеки нет", "ипотека жоқ", "ИПОТЕКА ЖОК!", "Ипотека: нет", "Не продаётся в ипотеку", "Ипотека не рассматривается"])
def test_explicit_refusal(text):
    assert parse_mortgage_text(text) is False


@pytest.mark.parametrize("text", ["Рассчитать ипотеку", "Квартира без ипотеки", "С ипотекой нет проблем", "Ипотека жоқ емес", "Кирпичный дом"])
def test_unknown_or_ambiguous_is_not_a_refusal(text):
    assert parse_mortgage_text(text) is None


def test_positive_and_scoped_html():
    assert parse_mortgage_text("Можно в ипотеку и без ипотеки") is True
    assert _mortgage_fields('<div class="offer__description"><p>Ипотеки нет</p></div>', []) is False
    assert _mortgage_fields('<div class="advertisement">Ипотека жок</div>', []) is None
    assert _mortgage_fields('', [{'mortgageSupported': False}]) is False
    assert _mortgage_fields('', [], 'Ипотека жоқ') is False


def test_refusal_lowers_both_scores_without_hiding_listing():
    args = dict(source_url='https://krisha.kz/a/show/123', title='Квартира', price_kzt=25_000_000, area_m2=60, rooms=2)
    ordinary = assess_listing(ListingCreate(**args), Settings())
    refusal = assess_listing(ListingCreate(**args, mortgage_supported=False), Settings())
    assert refusal.score == ordinary.score - 25
    assert refusal.verdict == 'СМОТРЕТЬ'
    search = dict(max_price=30_000_000, min_area=55, max_area=70)
    normal = analyze_search_result('2-комнатная квартира 60 м² 25 млн', None, **search)
    lowered = analyze_search_result('2-комнатная квартира 60 м² 25 млн', 'Ипотеки нет', **search)
    stored = analyze_search_result('2-комнатная квартира 60 м² 25 млн', None, mortgage_supported=False, **search)
    assert lowered['score'] == stored['score'] == normal['score'] - 25
    assert lowered['verdict'] == 'ПРОВЕРИТЬ'
    assert lowered['mortgage_supported'] is False


@pytest.mark.asyncio
async def test_server_import_reads_description_refusal(monkeypatch):
    html = '<meta property="og:description" content="Ипотека жоқ"><script type="application/ld+json">{"price":25000000,"area":60,"rooms":2}</script>'
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return httpx.Response(200, text=html, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    assert (await import_krisha_listing('https://krisha.kz/a/show/123')).mortgage_supported is False


@pytest.mark.asyncio
async def test_saved_refusal_survives_partial_import_and_updates_search(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_service, 'SessionLocal', sessions)
    async with sessions() as session:
        session.add(SearchResult(url='https://krisha.kz/a/show/123', title='Квартира', query='', search_engine='test'))
        await session.commit()
    payload = ListingCreate(source_url='https://krisha.kz/a/show/123', title='Квартира', price_kzt=25_000_000, area_m2=60, rooms=2, mortgage_supported=False)
    await listing_service.persist_listing(payload, notify=False, refresh_ai=False)
    saved = await listing_service.persist_listing(payload.model_copy(update={'mortgage_supported': None}), notify=False, refresh_ai=False)
    async with sessions() as session:
        row = await session.get(SearchResult, 1)
        assert row.mortgage_supported is False
        assert saved.row.mortgage_supported is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_backfills_known_refusal():
    from sqlalchemy import text
    from app.schema_migrations import ensure_legacy_columns
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("INSERT INTO listings (source, source_url, title, city, price_kzt, area_m2, rooms, is_full_two_room, mortgage_supported, bathrooms_count_is_lower_bound) VALUES ('manual','https://krisha.kz/a/show/123','Test','Астана',25000000,60,2,1,0,0)"))
        await connection.execute(text("INSERT INTO search_results (url,search_engine,query,title,status,import_status,imported_listing_id) VALUES ('https://krisha.kz/a/show/123','test','','Test','new:normal','imported',1)"))
        await connection.run_sync(ensure_legacy_columns)
        assert (await connection.execute(text('SELECT mortgage_supported FROM search_results'))).scalar() == 0
    await engine.dispose()
