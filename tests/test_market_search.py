import pytest
from types import SimpleNamespace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import search_agent
from app.krisha_search import build_krisha_search_url, parse_krisha_search_page
from app.models import Base, Listing, SearchResult
from app.search_providers import SearchItem


def card(ident, footer):
    return (f'<div class="a-card" data-id="{ident}">'
            f'<div class="a-card__main-info"><a class="a-card__title" href="/a/show/{ident}">'
            '4-комнатная квартира · 90 м²</a><div class="a-card__price">40 000 000 ₸</div></div>'
            f'{footer}</div>')


def test_explicit_structural_market_evidence_and_native_filter(monkeypatch):
    owner = '<div class="a-card__footer"><div class="label-user-owner">Хозяин недвижимости</div></div>'
    new = '<div class="a-card__footer user-complex"><div class="a-card__complex-label">Новостройка</div></div>'
    mobile = '<div class="a-card__bottom-label"><div class="label label-user-complex"><i></i>Новостройка</div></div>'
    html = card(101, owner) + card(102, new) + card(103, owner + new) + card(104, '<div>новостройка в описании</div>') + card(105, mobile)
    types = {item.url.rsplit('/', 1)[-1]:item.market_type for item in parse_krisha_search_page(html)}
    assert types == {'101':'secondary', '102':'new', '103':None, '104':None, '105':'new'}
    monkeypatch.setattr(search_agent.settings, 'market_type', 'new')
    assert 'das%5Bnovostroiki%5D=1' in build_krisha_search_url()
    monkeypatch.setattr(search_agent.settings, 'market_type', 'secondary')
    assert 'novostroiki' not in build_krisha_search_url()


@pytest.mark.asyncio
async def test_search_market_counts_and_imports_only_confirmed_types(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(search_agent, 'SessionLocal', sessions)
    monkeypatch.setattr(search_agent.settings, 'market_type', 'new')
    monkeypatch.setattr(search_agent.settings, 'search_city', 'astana')
    monkeypatch.setattr(search_agent.settings, 'target_rooms', 4)
    monkeypatch.setattr(search_agent.settings, 'min_area_m2', 80)
    monkeypatch.setattr(search_agent.settings, 'max_area_m2', 120)
    monkeypatch.setattr(search_agent.settings, 'min_price_kzt', None)
    monkeypatch.setattr(search_agent.settings, 'max_price_kzt', 50000000)
    title = '4-комнатная квартира · 90 м² · 40 000 000 ₸'
    async with sessions() as session:
        session.add(Listing(source_url='https://krisha.kz/a/show/102', title=title,
                            city='Астана', price_kzt=40000000, area_m2=90, rooms=4,
                            market_type='secondary'))
        await session.commit()
    count, accepted, candidates = await search_agent._save_items('krisha_direct', '4', [
        SearchItem(title, 'https://krisha.kz/a/show/101', market_type='new'),
        SearchItem(title, 'https://krisha.kz/a/show/102', market_type='new'),
        SearchItem(title, 'https://krisha.kz/a/show/103'),
    ])
    assert (count, accepted, [x.url for x in candidates]) == (1, 1, ['https://krisha.kz/a/show/101'])
    async with sessions() as session:
        assert len((await session.scalars(select(SearchResult))).all()) == 3
        assert (await session.scalar(select(Listing))).market_type == 'secondary'
    await engine.dispose()


@pytest.mark.asyncio
async def test_detail_market_conflict_prevents_import_and_records_evidence(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(search_agent, 'SessionLocal', sessions)
    monkeypatch.setattr(search_agent.settings, 'market_type', 'new')
    monkeypatch.setattr(search_agent.settings, 'auto_import_enabled', True)
    monkeypatch.setattr(search_agent.settings, 'auto_import_delay_seconds', 0)
    url = 'https://krisha.kz/a/show/101'
    async with sessions() as session:
        session.add(SearchResult(id=1, url=url, title='Квартира', query='', search_engine='krisha_direct', market_type='new'))
        await session.commit()
    async def fetch(*args, **kwargs):
        return SimpleNamespace(city='Астана', market_type='secondary')
    async def save(*args, **kwargs):
        pytest.fail('Mismatched market must not be persisted or notified')
    monkeypatch.setattr(search_agent, 'import_krisha_listing', fetch)
    monkeypatch.setattr(search_agent, 'persist_listing', save)
    assert await search_agent._auto_import([search_agent.SearchImportCandidate(1, url)]) == (0, 1, 0)
    async with sessions() as session:
        row = await session.get(SearchResult, 1)
        assert (row.market_type, row.import_status) == ('secondary', 'market_mismatch')
    await engine.dispose()


@pytest.mark.asyncio
async def test_secondary_uses_owner_filter_and_rejects_unmarked_advert(monkeypatch):
    monkeypatch.setattr(search_agent.settings, 'search_enabled', True)
    monkeypatch.setattr(search_agent.settings, 'market_type', 'secondary')
    monkeypatch.setattr(search_agent.settings, 'target_rooms', 2)
    monkeypatch.setattr(search_agent.settings, 'search_city', 'astana')
    received = []
    async def fetch(provider, query):
        assert (provider, query) == ('krisha_direct', 'owner:2')
        return provider, query, [SearchItem('owner', 'https://krisha.kz/a/show/1', seller_type='Хозяин недвижимости', market_type='secondary'),
                                 SearchItem('developer', 'https://krisha.kz/a/show/2', market_type='new')], None
    async def save(provider, query, items):
        received.extend(items)
        return 0, 0, []
    async def auto_import(candidates): return 0, 0, 0
    monkeypatch.setattr(search_agent, '_fetch_one', fetch)
    monkeypatch.setattr(search_agent, '_save_items', save)
    monkeypatch.setattr(search_agent, '_auto_import', auto_import)
    await search_agent._run_search()
    assert [item.url for item in received] == ['https://krisha.kz/a/show/1']
