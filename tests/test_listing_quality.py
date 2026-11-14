import pytest
import pytest_asyncio
from fastapi import FastAPI
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_quality as quality
from app.models import Base, DuplicateDecision, Listing, PriceSnapshot, SearchResult


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(quality, 'SessionLocal', sessions)
    async with sessions() as session:
        session.add_all([
            Listing(id=1, source='browser', source_url='https://krisha.kz/a/show/1', title='Квартира',
                    city='Астана', rooms=3, area_m2=80, price_kzt=40_000_000, bathrooms_count=None,
                    photo_urls=['https://photos.example/1.jpg'], approximate_latitude=51.13, approximate_longitude=71.43),
            Listing(id=2, source='manual', source_url='https://www.olx.kz/d/obyavlenie/test-IDabc.html', title='Другая',
                    city='Астана', rooms=3, area_m2=80, price_kzt=41_000_000, bathrooms_count=2,
                    repair_condition='хороший', photo_urls=['https://photos.example/2.jpg']),
            Listing(id=3, source='manual', source_url='https://krisha.kz/a/show/3', title='Похожая',
                    city='Астана', rooms=3, area_m2=80, price_kzt=40_000_000, bathrooms_count=3),
        ])
        session.add(DuplicateDecision(first_id=1, second_id=2, decision='same'))
        session.add(SearchResult(url='https://krisha.kz/a/show/1', city='astana', search_engine='test',query='',title='Квартира'))
        await session.flush()
        session.add(PriceSnapshot(listing_id=2, price_kzt=41_000_000, price_per_m2=512500))
        await session.commit()
    app = FastAPI(); app.include_router(quality.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        yield client, sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_quality_and_confirmed_merged_view_preserve_evidence_and_sources(env):
    client, _ = env
    result = (await client.get('/api/v1/listings/1/quality')).json()
    assert result['location_precision'] == 'approximate' and result['photo_count'] == 1
    assert result['fields']['bathrooms_count'] is False and 'bathrooms_count' in result['review_flags']
    merged = (await client.get('/api/v1/listings/1/merged-view')).json()
    assert [offer['listing_id'] for offer in merged['offers']] == [1,2]
    assert merged['fields']['bathrooms_count']['value'] == 2
    assert merged['fields']['bathrooms_count']['listing_id'] == 2
    assert merged['fields']['price_kzt']['value'] == 40_000_000
    assert 'price_kzt' in merged['conflicts']
    assert merged['offers'][1]['photo_urls'] == ['https://photos.example/2.jpg']
    assert merged['offers'][1]['price_history'][0]['price_kzt'] == 41_000_000


@pytest.mark.asyncio
async def test_audited_correction_and_validation_are_atomic(env):
    client, sessions = env
    endpoint = '/api/v1/listings/1/corrections'
    assert (await client.put(endpoint,json={'field':'price_kzt','value':1,'reason':'test'})).status_code == 422
    assert (await client.put(endpoint,json={'field':'bathrooms_count','value':2,'reason':' '})).status_code == 422
    saved = await client.put(endpoint,json={'field':'bathrooms_count','value':2,'reason':'Проверено на фото'})
    assert saved.status_code == 200 and saved.json()['changed']
    assert not (await client.put(endpoint,json={'field':'bathrooms_count','value':2,'reason':'Та же проверка'})).json()['changed']
    rows = (await client.get(endpoint)).json()['items']
    assert len(rows) == 1 and rows[0]['old_value'] is None and rows[0]['new_value'] == 2
    async with sessions() as session:
        listing = await session.get(Listing,1)
        result = await session.scalar(select(SearchResult).where(SearchResult.url == listing.source_url))
        assert listing.bathrooms_count == result.bathrooms_count == 2
        assert listing.price_kzt == 40_000_000


@pytest.mark.asyncio
async def test_manual_values_and_explicit_unknown_survive_import_and_backfills(env, monkeypatch):
    from app import listing_service
    from app.schemas import ListingCreate
    client, sessions = env
    monkeypatch.setattr(listing_service, 'SessionLocal', sessions)
    async def no_remote(payload, **kwargs): return payload, False, False
    monkeypatch.setattr(listing_service, '_enrich_twogis', no_remote)
    changes = [('bathrooms_count',None), ('repair_condition','Только вручную'),
               ('mortgage_supported',None), ('is_pledged',False), ('seller_type',None)]
    for field, value in changes:
        response = await client.put('/api/v1/listings/1/corrections',json={'field':field,'value':value,'reason':'Проверено лично'})
        assert response.status_code == 200 and response.json()['changed'], field
    incoming = ListingCreate(source='browser', source_url='https://krisha.kz/a/show/1', title='Квартира',
                             city='Астана', city_observed=True, rooms=3, area_m2=80, price_kzt=39_000_000,
                             bathrooms_count=3, repair_condition='евроремонт', mortgage_supported=True,
                             is_pledged=True, seller_type='Риелтор', details_extracted=True,
                             description='Продаю от хозяина, квартира в залоге, ипотека возможна.')
    await listing_service.persist_listing(incoming, notify=False, refresh_ai=False)
    await listing_service.backfill_repair_descriptions()
    await listing_service.backfill_mortgage_and_addresses()
    await listing_service.backfill_pledge_status()
    await listing_service.backfill_author_types()
    async with sessions() as session:
        await listing_service.fill_seller_from_krisha_search(session, 'https://krisha.kz/a/show/1', 'Хозяин недвижимости')
        await session.commit()
    async with sessions() as session:
        row = await session.get(Listing, 1)
        result = await session.scalar(select(SearchResult).where(SearchResult.url == row.source_url))
        assert (row.bathrooms_count, row.repair_condition, row.mortgage_supported, row.is_pledged, row.seller_type) == (None,'Только вручную',None,False,None)
        assert (row.repair_source_kind,row.repair_evidence) == ('manual',None)
        assert row.price_kzt == 39_000_000
        assert (result.bathrooms_count,result.mortgage_supported,result.is_pledged,result.seller_type) == (None,None,False,None)


@pytest.mark.asyncio
async def test_simultaneous_corrections_record_actual_prior_value(env):
    import asyncio
    client, _ = env
    endpoint = '/api/v1/listings/1/corrections'
    async def change(value):
        return await client.put(endpoint, json={'field':'bathrooms_count','value':value,'reason':'Проверено лично'})
    first, second = await asyncio.gather(change(2), change(3))
    assert first.status_code == second.status_code == 200
    rows = (await client.get(endpoint)).json()['items']
    assert len(rows) == 2
    assert rows[0]['old_value'] == rows[1]['new_value']
    assert {rows[0]['new_value'], rows[1]['new_value']} == {2,3}
