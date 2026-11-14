import csv
import io

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_export
from app.models import Base, Listing


@pytest.mark.asyncio
async def test_export_all_and_defend_csv_formulas(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(listing_export, 'SessionLocal', sessions)
    async with sessions.begin() as session:
        for id, title, city in [(1, '=1+1', 'Астана'), (2, ' \t@cmd', 'Экибастуз')]:
            session.add(Listing(id=id, source='manual', source_url=f'https://krisha.kz/a/show/{id}',
                title=title, city=city, rooms=3, area_m2=80, price_kzt=35000000, description='PRIVATE_DOCUMENT_CONTENT'))
    app = FastAPI(); app.include_router(listing_export.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        result = await client.get('/api/v1/listings/export?format=csv')
        assert result.status_code == 200 and result.content.startswith(b'\xef\xbb\xbf')
        rows = list(csv.DictReader(io.StringIO(result.content.decode('utf-8-sig'))))
        assert len(rows) == 2 and rows[0]['Название'] == "'=1+1" and rows[1]['Название'] == "' \t@cmd"
        assert rows[0]['Цена ₸'] == '35000000'
        assert 'PRIVATE_DOCUMENT_CONTENT' not in result.text
        assert result.headers['content-disposition'].startswith('attachment;')
        js = (await client.get('/api/v1/listings/export?format=json')).json()
        assert js['scope'] == 'all_saved_listings' and len(js['items']) == 2
        assert js['items'][0]['title'] == '=1+1' and 'description' not in js['items'][0]
        assert (await client.get('/api/v1/listings/export?format=bad')).status_code == 422
    await engine.dispose()


@pytest.mark.parametrize('text', ['=cmd', '+1', '-1', '@x', '\tcmd', '\rX', '\nX', '  =SUM(1,1)', ' \t@x', ' \tcmd'])
def test_csv_cell_protects_formula_and_controls(text):
    assert listing_export.safe_csv_cell(text) == "'" + text


def test_export_source_url_drops_credentials_query_and_fragment():
    assert listing_export._public_url('https://user:secret@example.com/listing/1?token=secret#private') == 'https://example.com/listing/1'
    assert listing_export._public_url('javascript:alert(1)') == ''
