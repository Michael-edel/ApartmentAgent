import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import inspection
from app.models import Base, Listing


@pytest.mark.asyncio
async def test_inspection_and_private_file_roundtrip(monkeypatch):
    pytest.importorskip('aiosqlite')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(inspection, 'SessionLocal', sessions)
    async with sessions.begin() as session:
        session.add(Listing(id=1, source='manual', source_url='https://krisha.kz/a/show/1',
            title='Квартира', city='Астана', rooms=3, area_m2=90, price_kzt=40000000))
    app = FastAPI(); app.include_router(inspection.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        url = '/api/v1/listings/1/inspection'
        assert (await client.get(url)).json()['outcome'] == 'undecided'
        body = {'checks':{'meters':True,'heating':False},'seller_questions':'Кто собственник?', 'notes':'Двор', 'outcome':'needs_review'}
        assert (await client.put(url, json=body)).status_code == 200
        assert (await client.get(url)).json()['checks'] == body['checks']
        assert (await client.put(url, json={**body, 'checks':{'invalid':True}})).status_code == 422
        assert (await client.get('/api/v1/listings/999/inspection')).status_code == 404
        data = b'\x89PNG\r\n\x1a\nfixture'
        saved = await client.post(url+'/attachments', params={'category':'meters','name':'фото.png'}, content=data)
        assert saved.status_code == 201
        attachment_url = url + '/attachments/' + str(saved.json()['id'])
        download = await client.get(attachment_url)
        assert download.content == data
        assert download.headers['content-disposition'].startswith('attachment;')
        assert download.headers['x-content-type-options'] == 'nosniff'
        assert (await client.get('/api/v1/listings/2/inspection/attachments/'+str(saved.json()['id']))).status_code == 404
        assert (await client.post(url+'/attachments', params={'category':'meters','name':'x.png'}, content=b'<script>')).status_code == 415
        assert (await client.post(url+'/attachments', params={'category':'meters','name':'big.png'}, content=data + b'x' * inspection.MAX_FILE)).status_code == 413
        assert (await client.delete(attachment_url)).status_code == 204
        assert (await client.get(attachment_url)).status_code == 404
    await engine.dispose()
