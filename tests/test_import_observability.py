from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app import import_observability as obs
from app.models import Base


@pytest_asyncio.fixture
async def environment(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(obs, 'SessionLocal', sessions)
    clock = {'value':datetime(2026, 11, 13, tzinfo=UTC)}
    monkeypatch.setattr(obs, '_now', lambda: clock['value'])
    yield sessions, clock
    await engine.dispose()


@pytest.mark.asyncio
async def test_backoff_durable_per_source_and_success_resets(environment):
    _, clock = environment
    first = await obs.record_import('krisha','https://krisha.kz/a/show/123?token=secret','search','error',http_status=468)
    assert first['wait_seconds'] == 60
    assert not (await obs.source_cooldown('olx'))['in_cooldown']
    clock['value'] += timedelta(seconds=61)
    assert not (await obs.source_cooldown('krisha'))['in_cooldown']
    second = await obs.record_import('krisha','https://krisha.kz/a/show/123','search','blocked',http_status=429)
    assert second['wait_seconds'] == 120
    for _ in range(8):
        last = await obs.record_import('krisha',None,'search','blocked',http_status=403)
    assert last['wait_seconds'] == 3600
    await obs.record_import('olx','https://www.olx.kz/d/obyavlenie/test-IDabc.html','browser','success')
    assert (await obs.source_cooldown('krisha'))['in_cooldown']
    await obs.record_import('krisha',None,'search','success')
    assert (await obs.source_cooldown('krisha'))['blocked_count'] == 0


@pytest.mark.asyncio
async def test_journal_stores_only_safe_metadata_and_bounded_count(environment, monkeypatch):
    monkeypatch.setattr(obs, '_MAX_EVENTS_PER_SOURCE', 2)
    for index in range(3):
        await obs.record_import('krisha',f'https://krisha.kz/a/show/{index}?access_token=SECRET',
                                'browser','error',fields=['title','price_kzt','password', 'phone'],
                                error='Bearer SECRET password=SECRET')
    result = await obs.import_observability_events(source='krisha', limit=20)
    assert len(result['items']) == 2
    assert result['items'][0]['fields'] == ['price_kzt','title']
    assert 'SECRET' not in str(result)
    assert result['items'][0]['url'] == 'https://krisha.kz/a/show/2'
    assert obs._safe_url('krisha','https://krisha.kz@evil.example/a/show/1') == ''
    state = await obs.import_observability_status()
    assert state['sources']['krisha']['counts'] == {'error':2}


@pytest.mark.asyncio
async def test_browser_presence_expires_without_polluting_the_import_journal(environment):
    _, clock = environment
    await obs.observe_browser_presence('0.4.14')
    state = await obs.import_observability_status()
    assert state['browser_connected'] and state['browser_version'] == '0.4.14'
    assert state['last_browser_activity_at'] is None
    clock['value'] += timedelta(minutes=3)
    assert not (await obs.import_observability_status())['browser_connected']
