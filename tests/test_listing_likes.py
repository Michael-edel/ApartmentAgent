import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import listing_service, main, search_agent
from app.models import Base, SearchResult
from app.schemas import ListingCreate


@pytest.mark.asyncio
async def test_likes_share_canonical_url_across_import_and_both_lists(monkeypatch, tmp_path):
    pytest.importorskip("aiosqlite")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'likes.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(listing_service, "SessionLocal", sessions)
    for settings in (main.settings, search_agent.settings):
        for key, value in {"target_rooms": 2, "min_area_m2": 50, "max_area_m2": 100,
                           "max_price_kzt": 50_000_000, "min_bathrooms": 0,
                           "exclude_pledged": False, "twogis_enabled": False}.items():
            monkeypatch.setattr(settings, key, value)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async with sessions() as session:
        for index in range(203):
            url = ("http://www.krisha.kz/a/show/123/?x=1" if index == 0
                   else f"https://krisha.kz/a/show/{1000 + index}")
            session.add(SearchResult(url=url, city="astana", search_engine="test", query="test",
                                     title="2-комнатная квартира 70 м² 30000000 ₸", status="new:normal",
                                     first_seen=start + timedelta(seconds=index)))
        session.add(SearchResult(url="https://krisha.kz/a/show/123", city="astana", search_engine="test", query="test",
                                 title="2-комнатная квартира 70 м² 30000000 ₸", status="new:normal",
                                 first_seen=start + timedelta(seconds=300)))
        await session.commit()
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.put('/api/v1/search/results/1/liked', json={"liked": "true"})).status_code == 422
        assert (await client.put('/api/v1/search/results/9999/liked', json={"liked": True})).status_code == 404
        for _ in range(2):
            result = await client.put('/api/v1/search/results/1/liked', json={"liked": True})
            assert result.status_code == 200 and result.json() == {"liked": True}
        concurrent = await asyncio.gather(*(
            client.put('/api/v1/search/results/1/liked', json={"liked": True}) for _ in range(4)
        ))
        assert all(response.status_code == 200 and response.json() == {"liked": True} for response in concurrent)
        sorted_rows = (await client.get('/api/v1/search/results', params={"limit": 2, "sort": "liked"})).json()
        assert [row['id'] for row in sorted_rows] == [204, 1]
        assert all(row['liked'] for row in sorted_rows)
        assert (await client.get('/api/v1/search/results', params={"limit": 1})).json()[0]['id'] == 204
        saved = await listing_service.persist_listing(
            ListingCreate(source_url='https://krisha.kz/a/show/123', title='Квартира',
                          price_kzt=30_000_000, area_m2=70, rooms=2),
            notify=False, refresh_ai=False,
        )
        listing_id = saved.row.id
        assert (await client.get(f'/api/v1/listings/{listing_id}')).json()['liked'] is True
        assert (await client.get('/api/v1/listings')).json()[0]['liked'] is True
        assert (await client.put(f'/api/v1/listings/{listing_id}/liked', json={"liked": False})).json() == {"liked": False}
        assert (await client.get(f'/api/v1/listings/{listing_id}')).json()['liked'] is False
        rows = (await client.get('/api/v1/search/results', params={"limit": 200, "sort": "liked"})).json()
        assert not any(row['liked'] for row in rows)
    await engine.dispose()
