from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.property_duplicates import duplicate_evidence, group_listings
from app.market_comparison import compare_saved_listings


NOW = datetime(2026, 11, 12, tzinfo=UTC)


def listing(index, **overrides):
    data = dict(id=index, source_url=f'https://krisha.kz/a/show/{index}', city='Астана',
                address='улица Сарайшык 34', rooms=3, area_m2=90.0,
                floor=5, floors_total=9, bathrooms_count=2, photo_urls=[],
                title=f'Квартира {index}', price_kzt=40_000_000,
                last_imported_at=NOW - timedelta(minutes=index))
    data.update(overrides)
    return SimpleNamespace(**data)


def test_house_floor_area_and_two_shared_photos_are_conservative():
    target = listing(1)
    assert duplicate_evidence(target, listing(2, address='ул. Сарайшык, 34', area_m2=90.4))['kind'] == 'same_house_floor_area'
    assert duplicate_evidence(target, listing(2, address='улица Сарайшык')) is None
    assert duplicate_evidence(target, listing(2, floor=6)) is None
    assert duplicate_evidence(target, listing(2, floors_total=12)) is None
    assert duplicate_evidence(target, listing(2, area_m2=90.6)) is None
    assert duplicate_evidence(target, listing(2, bathrooms_count=1)) is None
    assert duplicate_evidence(listing(1, address=None), listing(2, address=None)) is None
    photos = ['https://photos.example/a.jpg?size=1', 'https://photos.example/b.jpg?size=1']
    a = listing(1, address=None, floor=None, photo_urls=photos)
    b = listing(2, address=None, floor=None, photo_urls=photos)
    assert duplicate_evidence(a, b)['kind'] == 'shared_photos'
    assert duplicate_evidence(a, listing(2, address=None, floor=None, photo_urls=photos[:1])) is None
    # URL query parameters may identify different photos and must not be stripped.
    assert duplicate_evidence(a, listing(2, address=None, floor=None,
                                        photo_urls=[url.replace('size=1', 'size=2') for url in photos])) is None


def test_duplicate_groups_do_not_bridge_pairwise_incompatible_offers():
    first = listing(1, area_m2=90.0)
    bridge = listing(2, area_m2=90.4)
    far = listing(3, area_m2=90.8)
    assert duplicate_evidence(first, bridge)
    assert duplicate_evidence(bridge, far)
    assert duplicate_evidence(first, far) is None
    groups = group_listings([first, bridge, far], target=first)
    assert {row.id for row in groups[0]} == {1, 2}
    assert all(not ({1, 3} <= {row.id for row in group}) for group in groups)


def test_lower_bound_bathroom_compatibility():
    lower = listing(1, bathrooms_count=2, bathrooms_count_is_lower_bound=True)
    assert duplicate_evidence(lower, listing(2, bathrooms_count=3))
    assert duplicate_evidence(lower, listing(2, bathrooms_count=3, bathrooms_count_is_lower_bound=True))
    assert duplicate_evidence(lower, listing(2, bathrooms_count=1)) is None
    assert duplicate_evidence(listing(1, bathrooms_count=2), listing(2, bathrooms_count=3)) is None


def test_known_krisha_photo_renditions_normalize_only_matching_uuid_and_index():
    photo_id = 'd4159205-1181-4ed5-a25f-4b8e048e10d4'
    def images(size, host='alaps-photos-kr.kcdn.kz'):
        return [f'https://{host}/webp/d4/{photo_id}/{index}-{size}.webp' for index in (12, 13)]
    first = listing(1, address=None, photo_urls=images('750x470'))
    second = listing(2, address=None, photo_urls=images('100x100'))
    assert duplicate_evidence(first, second) == {'kind': 'shared_photos', 'shared_photos': 2}
    # A single image in multiple sizes remains one identity, not two photos.
    first.photo_urls = [images('750x470')[0], images('100x100')[0]]
    second.photo_urls = first.photo_urls[:]
    assert duplicate_evidence(first, second) is None
    first.photo_urls = images('750x470', host='example.com')
    second.photo_urls = images('100x100', host='example.com')
    assert duplicate_evidence(first, second) is None


def test_comparable_groups_exclude_target_alias_and_sold_but_keep_offer_history():
    common = dict(district='Есиль', residential_complex='ЖК Мечта', latitude=51.1283,
                  longitude=71.4306, price_kzt=40_000_000, area_m2=90,
                  sold_at=None, source_archived_at=None, source_unavailable_at=None)
    target = listing(1, **common, seller_type='Хозяин недвижимости')
    target_alias = listing(2, **common, seller_type='Риелтор')
    owner = listing(3, **{**common, 'address': 'Сарайшык 35', 'latitude': 51.129},
                    seller_type='Хозяин недвижимости')
    newer_agent = listing(4, **{**common, 'address': 'Сарайшык 35', 'latitude': 51.129,
                                'last_imported_at': NOW, 'price_kzt': 43_000_000}, seller_type='Риелтор')
    sold = listing(5, **{**common, 'address': 'Сарайшык 36', 'latitude': 51.129,
                         'sold_at': NOW}, seller_type='Хозяин недвижимости')
    sold_alias = listing(6, **{**common, 'address': 'Сарайшык 36', 'latitude': 51.129}, seller_type='Риелтор')
    archived_owner = listing(7, **{**common, 'address': 'Сарайшык 37', 'latitude': 51.129,
                                   'source_archived_at': NOW}, seller_type='Хозяин недвижимости')
    active_agent = listing(8, **{**common, 'address': 'Сарайшык 37', 'latitude': 51.129}, seller_type='Риелтор')
    rows = [target, target_alias, owner, newer_agent, sold, sold_alias, archived_owner, active_agent]
    groups = compare_saved_listings(target, rows, now=NOW, include_comparables=True)['comparables']
    items = groups['same_complex']['items']
    assert groups['same_complex']['count'] == 2
    assert {item['listing_id'] for item in items} == {3, 8}
    first = next(item for item in items if item['listing_id'] == 3)
    assert first['offer_count'] == 2
    assert {offer['price_kzt'] for offer in first['offers']} == {40_000_000, 43_000_000}
    second = next(item for item in items if item['listing_id'] == 8)
    assert {offer['availability'] for offer in second['offers']} == {'active', 'archived'}


def test_comparable_keeps_owner_offer_and_uses_active_alias_location_when_missing():
    common = dict(district='Есиль', rooms=3, area_m2=90, price_kzt=40_000_000,
                  sold_at=None, source_archived_at=None, source_unavailable_at=None)
    target = listing(1, **common, residential_complex='ЖК Мечта', latitude=51.1283,
                     longitude=71.4306, seller_type='Хозяин недвижимости')
    photos = ['https://photos.example/a.jpg', 'https://photos.example/b.jpg']
    owner = listing(2, **common, residential_complex=None, address=None, latitude=None,
                    longitude=None, photo_urls=photos, seller_type='Хозяин недвижимости')
    agent = listing(3, **common, residential_complex='ЖК Мечта', address='Сарайшык 35',
                    latitude=51.129, longitude=71.4306, photo_urls=photos, seller_type='Риелтор')
    result = compare_saved_listings(target, [target, owner, agent], now=NOW, include_comparables=True)
    items = result['comparables']['same_complex']['items']
    assert len(items) == 1 and items[0]['listing_id'] == 2 and items[0]['offer_count'] == 2
    assert items[0]['location_source_listing_id'] == 3
    assert items[0]['complex_source_listing_id'] == 3
    assert items[0]['distance_m'] is not None


@pytest.mark.asyncio
async def test_duplicates_api_returns_distinct_offers_and_status(monkeypatch):
    pytest.importorskip('aiosqlite')
    from app import main
    from app.models import Base, Listing
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    async with sessions() as session:
        for index in (1, 2):
            session.add(Listing(source='manual', source_url=f'https://krisha.kz/a/show/{index}',
                                title=f'Квартира {index}', city='Астана', address='Сарайшык 34',
                                price_kzt=40_000_000 + index, area_m2=90, rooms=3, floor=5,
                                floors_total=9, bathrooms_count=2 if index == 1 else 3,
                                bathrooms_count_is_lower_bound=index == 1,
                                seller_type='Хозяин недвижимости' if index == 1 else 'Риелтор',
                                sold_at=NOW if index == 2 else None))
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        result = (await client.get('/api/v1/listings/1/duplicates')).json()
        assert result['status'] == 'probable' and result['listing_id'] == 1
        assert len(result['items']) == 2 and result['items'][0]['is_current'] is True
        assert result['items'][1]['availability'] == 'sold'
        assert result['items'][1]['seller_category'] == 'realtor'
        assert result['items'][1]['evidence']['kind'] == 'same_house_floor_area'
        assert (await client.get('/api/v1/listings/999/duplicates')).status_code == 404
    await engine.dispose()
