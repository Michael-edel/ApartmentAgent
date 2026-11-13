from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import raiseload

from app.cities import city_slug
from app.models import Listing, ListingLike, ListingObservation, ListingTargetPrice, SearchResult
from app.property_duplicates import (availability, canonical_url, confirmed_component, group_listings,
                                     load_duplicate_decisions)


TRACKED_FIELDS = ('price_kzt', 'description', 'repair_condition', 'mortgage_supported', 'is_pledged',
                  'title', 'area_m2', 'rooms', 'bathrooms_count', 'floor', 'floors_total',
                  'building_year', 'building_type', 'seller_type', 'address', 'city', 'property_details')


def snapshot(row):
    return {key: getattr(row, key) for key in TRACKED_FIELDS}


def changed_fields(before, row):
    # Compare persisted facts rather than incoming payload; omitted fields cannot
    # erase an observation. Copy JSON values when taking the initial snapshot.
    return {key: {'old': before[key], 'new': getattr(row, key)} for key in TRACKED_FIELDS
            if before[key] != getattr(row, key)}


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _earliest(row, first_seen):
    dates = [v for v in (row.created_at, first_seen.get(canonical_url(row.source_url))) if v is not None]
    return min((_utc(v) for v in dates), default=None)


def _age(value):
    return max(0, (datetime.now(UTC) - value).days) if value else None


async def _context(session, listing_id):
    rows = list((await session.scalars(select(Listing).options(raiseload('*')))).all())
    selected = next((row for row in rows if row.id == listing_id), None)
    if not selected:
        return None
    decisions = await load_duplicate_decisions(session)
    group = next(group for group in group_listings(rows, target=selected, decisions=decisions) if selected in group)
    confirmed = confirmed_component(selected.id, decisions)
    return selected, group, confirmed


def _matching(selected, group, confirmed, target):
    if not target:
        return []
    matches = []
    for row in group:
        if row.id != selected.id and not target.include_similar:
            continue
        if availability(row) != 'active' or row.price_kzt > target.target_price_kzt:
            continue
        matches.append({'listing_id': row.id, 'source_url': canonical_url(row.source_url),
                        'price_kzt': row.price_kzt, 'relationship': 'self' if row.id == selected.id else
                        'confirmed' if row.id in confirmed else 'probable'})
    return sorted(matches, key=lambda item: (item['price_kzt'], item['listing_id']))


async def target_price_matches(session, listing_id):
    context = await _context(session, listing_id)
    if context is None:
        return []
    target = await session.get(ListingTargetPrice, listing_id)
    return _matching(*context, target)


async def tracking_summary(session, listing_id):
    context = await _context(session, listing_id)
    if context is None:
        return None
    selected, group, confirmed = context
    observed = await session.execute(select(SearchResult.url, SearchResult.first_seen))
    first_seen = {}
    for url, first in observed:
        if first is not None:
            key = canonical_url(url)
            first_seen[key] = min(_utc(first), first_seen.get(key, _utc(first)))
    own_first = _earliest(selected, first_seen)
    others = [(row, _earliest(row, first_seen)) for row in group if row.id != selected.id]
    others = [(row, date) for row, date in others if date is not None]
    earlier = [(row, date) for row, date in others if own_first is None or date < own_first]
    oldest = min(earlier, key=lambda item: (item[1], item[0].id)) if earlier else None
    observations = (await session.scalars(select(ListingObservation).where(ListingObservation.listing_id == listing_id)
                    .order_by(ListingObservation.observed_at.desc(), ListingObservation.id.desc()))).all()
    target = await session.get(ListingTargetPrice, listing_id)
    likes = (await session.scalars(select(ListingLike.url))).all()
    liked = canonical_url(selected.source_url) in {canonical_url(url) for url in likes}
    return {'first_seen_at': own_first, 'listing_age_days': _age(own_first),
            'related_first_seen_at': oldest[1] if oldest else None,
            'related_age_days': _age(oldest[1]) if oldest else None,
            'related_confidence': ('confirmed' if oldest[0].id in confirmed else 'probable') if oldest else None,
            'related_offer': {'listing_id': oldest[0].id, 'source_url': canonical_url(oldest[0].source_url),
                              'price_kzt': oldest[0].price_kzt} if oldest else None,
            'changes': [{'id': item.id, 'observed_at': item.observed_at, 'changes': item.changes} for item in observations],
            'target': {'target_price_kzt': target.target_price_kzt, 'include_similar': target.include_similar,
                       'updated_at': target.updated_at, 'paused': not liked} if target else None,
            'matching_offers': _matching(selected, group, confirmed, target)}

async def evaluate_target_alerts(listing_id=None, *, city=None):
    """Evaluate configured liked listings after an import or a target update."""
    from app.config import get_settings
    from app.database import SessionLocal
    from app.telegram import notify_target_price

    settings = get_settings()
    if not settings.telegram_enabled or not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    async with SessionLocal() as session:
        targets = list((await session.scalars(select(ListingTargetPrice))).all())
        likes = {canonical_url(url) for url in (await session.scalars(select(ListingLike.url))).all()}
        events = []
        for target in targets:
            if listing_id is not None and target.listing_id != listing_id:
                continue
            context = await _context(session, target.listing_id)
            if (context is None or canonical_url(context[0].source_url) not in likes
                    or (city is not None and city_slug(context[0].city) != city)):
                continue
            for offer in _matching(*context, target):
                events.append({'listing_id':target.listing_id, 'offer_id':offer['listing_id'],
                               'price_kzt':offer['price_kzt'], 'target_kzt':target.target_price_kzt,
                               'target_updated_at':target.updated_at, 'source_url':offer['source_url'],
                               'relationship':offer['relationship']})
    for event in events:
        await notify_target_price(**event)
