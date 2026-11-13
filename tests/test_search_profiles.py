import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import search_agent, search_profiles
from app.config import search_settings_override
from app.models import Base, SearchPreference, SearchProfile
from app.search_conditions import SearchConditions, evaluate_conditions, kitchen_area


def test_conditions_unknown_hard_and_soft():
    spec = SearchConditions.model_validate({'first_floor': 'hard', 'last_floor': 'preference',
        'min_kitchen_m2': {'value': 12, 'mode': 'hard'}})
    assert not evaluate_conditions(spec, floor=2, floors_total=9)['matches_hard']
    result = evaluate_conditions(spec, floor=9, floors_total=9, kitchen_m2=14)
    assert result['matches_hard'] and result['score_adjustment'] == -10
    assert kitchen_area({'Площадь кухни': '12,5 м²'}) == 12.5
    assert kitchen_area(text='кухня 14 м²') == 14
    assert kitchen_area(text='ремонт кухни в 2020 году') is None


@pytest.mark.asyncio
async def test_profile_activation_and_isolated_monitor(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(search_profiles, 'SessionLocal', sessions)
    monkeypatch.setattr(search_agent, 'SessionLocal', sessions)
    monkeypatch.setattr(search_agent, '_search_lock', asyncio.Lock())
    monkeypatch.setattr(search_agent.settings, 'search_city', 'astana')
    monkeypatch.setattr(search_agent.settings, 'search_conditions', {})
    for name in ('target_rooms', 'min_price_kzt', 'max_price_kzt', 'min_area_m2', 'max_area_m2', 'min_bathrooms', 'exclude_pledged'):
        monkeypatch.setattr(search_agent.settings, name, getattr(search_agent.settings, name))
    body = search_profiles.ProfileBody(name='Астана', enabled=True, criteria={
        'city': 'astana', 'rooms': 3, 'max_price_kzt': 40000000, 'min_area_m2': 80,
        'conditions': {'first_floor': 'hard'}})
    row = await search_profiles.create_profile(body)
    visited = []
    async def fake_run():
        visited.append((search_agent.settings.search_city, search_settings_override.get() is not None))
    monkeypatch.setattr(search_agent, '_run_search', fake_run)
    await search_agent.run_monitored_profiles()
    assert visited == [('astana', True)]
    assert search_agent.settings.search_city == 'astana'
    await search_profiles.activate_profile(row['id'])
    assert search_agent.settings.search_city == 'astana'
    assert search_agent.settings.search_conditions['first_floor'] == 'hard'
    assert (await search_profiles.list_profiles())['active_profile_id'] == row['id']
    await engine.dispose()


@pytest.mark.asyncio
async def test_ekibastuz_is_manual_but_astana_background_continues(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(search_agent, 'SessionLocal', sessions)
    monkeypatch.setattr(search_profiles, 'SessionLocal', sessions)
    monkeypatch.setattr(search_agent, '_search_lock', asyncio.Lock())
    monkeypatch.setattr(search_agent.settings, 'search_city', 'ekibastuz')
    criteria = {'rooms': 3, 'max_price_kzt': 40000000, 'min_area_m2': 80}
    async with sessions() as session:
        session.add_all([
            SearchProfile(id=1, name='Эк', enabled=True, criteria={**criteria, 'city':'ekibastuz'}),
            SearchProfile(id=2, name='Старый Алматы', enabled=True, criteria={**criteria, 'city':'almaty'}),
            SearchPreference(id=1, rooms=2, city='ekibastuz', active_profile_id=1),
        ])
        await session.commit()
    seen = []
    async def fake_run():
        seen.append((search_agent.settings.search_city, search_agent.settings.auto_import_enabled))
    monkeypatch.setattr(search_agent, '_run_search', fake_run)
    class Once:
        stopped = False
        def is_set(self): return self.stopped
        async def wait(self): self.stopped = True
    await search_agent.periodic_search(Once())
    assert seen == [('astana', False)]
    assert search_agent.settings.search_city == 'ekibastuz'
    profiles = await search_profiles.list_profiles()
    assert [row['id'] for row in profiles['items']] == [1]
    async with sessions() as session:
        assert (await session.get(SearchProfile, 2)).criteria['city'] == 'almaty'
    await engine.dispose()
