import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select, update

from app.availability import availability_key, set_source_availability, unavailable_urls
from app.config import get_settings, SearchSettingsProxy, search_settings_override
from app.cities import CITY_NAMES, SEARCH_CITIES, KnownCity, SearchCity, city_matches, city_slug
from app.database import SessionLocal
from app.importer import ListingArchived, ListingImportError, ListingUnavailable, import_krisha_listing
from app.krisha_search import KrishaSearchBlocked, normalize_krisha_listing_url
from app.listing_service import fill_seller_from_krisha_search, persist_listing, sync_search_result_facts
from app.models import Listing, SearchHistory, SearchPreference, SearchProfile, SearchResult
from app.mortgage import parse_mortgage_text
from app import olx
from app.property_duplicates import canonical_url
from app.schemas import ListingCreate
from app.scoring import listing_river_bank
from app.search_analysis import analyze_search_result, is_target_search_result, parse_bathrooms_count, bathrooms_is_lower_bound
from app.search_providers import PROVIDERS, SearchItem
from app.search_conditions import SearchConditions, evaluate_conditions, kitchen_area
from app.market_type import MarketFilter, matches_market_type
from app.seller import seller_category

settings = SearchSettingsProxy()
logger = logging.getLogger(__name__)
_search_lock = asyncio.Lock()

DISTRICTS = ["Есильский район", "Нура район", "Алматы район", "Сарыарка район", "Байконур район"]
QUERYLESS_PROVIDERS = {"krisha_direct", "olx_direct"}


class InvalidSearchArea(ValueError):
    pass


class InvalidSearchPrice(ValueError):
    pass


@dataclass(slots=True)
class SearchSummary:
    queries: int = 0
    found: int = 0
    new: int = 0
    errors: int = 0
    blocked: int = 0
    imported: int = 0
    import_errors: int = 0
    import_blocked: int = 0
    providers: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(slots=True)
class SearchImportCandidate:
    result_id: int
    url: str
    city: SearchCity = "astana"
    payload: ListingCreate | None = None


def _priority(title: str, snippet: str | None, mortgage_supported: bool | None = None,
              is_pledged: bool | None = None,
              river_bank: Literal['left', 'right', 'unknown'] = 'unknown') -> str:
    result = analyze_search_result(
        title,
        snippet,
        max_price=settings.max_price_kzt,
        min_price=settings.min_price_kzt,
        min_area=settings.min_area_m2,
        max_area=settings.max_area_m2,
        target_rooms=settings.target_rooms,
        mortgage_supported=mortgage_supported,
        is_pledged=is_pledged,
        river_bank=river_bank,
    )
    if result["score"] >= 85:
        return "urgent"
    if result["score"] >= 65:
        return "good"
    return "normal"


def _matches_target(item: SearchItem) -> bool:
    return is_target_search_result(
        item.title,
        item.snippet,
        max_price=settings.max_price_kzt,
        min_price=settings.min_price_kzt,
        min_area=settings.min_area_m2,
        max_area=settings.max_area_m2,
        target_rooms=settings.target_rooms,
    )


def bathrooms_for_row(row: SearchResult) -> int | None:
    count = getattr(row, 'bathrooms_count', None)
    return count if count is not None else parse_bathrooms_count(f"{row.title or ''} {row.snippet or ''}")


def conditions_for_listing(listing, conditions=None):
    return evaluate_conditions(settings.search_conditions if conditions is None else conditions, floor=getattr(listing, 'floor', None),
        floors_total=getattr(listing, 'floors_total', None), building_year=getattr(listing, 'building_year', None),
        kitchen_m2=kitchen_area(getattr(listing, 'property_details', None)))


def conditions_for_result(row, listing=None, conditions=None):
    from app.search_analysis import _FLOOR_PATTERN, _YEAR_PATTERN
    text = f"{row.title or ''} {row.snippet or ''}"
    floor_match, year_match = _FLOOR_PATTERN.search(text), _YEAR_PATTERN.search(text)
    floor = getattr(listing, 'floor', None) if listing else None
    total = getattr(listing, 'floors_total', None) if listing else None
    year = getattr(listing, 'building_year', None) if listing else None
    kitchen = kitchen_area(getattr(listing, 'property_details', None)) if listing else None
    return evaluate_conditions(settings.search_conditions if conditions is None else conditions,
        floor=floor if floor is not None else int(floor_match.group('floor')) if floor_match else None,
        floors_total=total if total is not None else int(floor_match.group('total')) if floor_match else None,
        building_year=year if year is not None else int(year_match.group('value') or year_match.group('year_suffix')) if year_match else None,
        kitchen_m2=kitchen if kitchen is not None else kitchen_area(text=text))


def _row_matches_target(row: SearchResult) -> bool:
    bathrooms = bathrooms_for_row(row)
    return _row_matches_basic(row) and (not settings.min_bathrooms or (
        bathrooms is not None and bathrooms >= settings.min_bathrooms
    ))


def _row_hidden_by_pledge(row: SearchResult) -> bool:
    return settings.exclude_pledged and getattr(row, "is_pledged", None) is True


def _row_matches_basic(row: SearchResult, *, include_pledged: bool = False) -> bool:
    if not include_pledged and _row_hidden_by_pledge(row):
        return False
    return is_target_search_result(
        row.title,
        row.snippet,
        max_price=settings.max_price_kzt,
        min_price=settings.min_price_kzt,
        min_area=settings.min_area_m2,
        max_area=settings.max_area_m2,
        target_rooms=settings.target_rooms,
    )


def _rotated_queries() -> list[str]:
    rooms = settings.target_rooms
    name = CITY_NAMES[settings.search_city]
    queries = [f"{name} квартиры продажа krisha.kz"]
    if settings.search_city == 'astana':
        queries.append('Астана квартиры продажа левый берег krisha.kz')
    queries += [f"{name} {count}-комнатная квартира продажа krisha.kz" for count in range(rooms, 6)]
    if settings.search_city == 'astana':
        queries += [f"{name} квартиры продажа {district} krisha.kz" for district in DISTRICTS]
    count = min(max(settings.search_queries_per_run, 1), len(queries))
    slot = int(datetime.now(UTC).timestamp() // (max(settings.search_interval_minutes, 15) * 60))
    start = (slot * count) % len(queries)
    return [queries[(start + offset) % len(queries)] for offset in range(count)]


_CITY_WORDS = {
    'astana': r'астана|astana|нур[- ]?султан|нұр[- ]?сұлтан',
    'almaty': r'алматы|алма[- ]?ата|almaty',
    'shymkent': r'шымкент|shymkent',
    'karaganda': r'караганда|қарағанды|karaganda|karagandy',
    'pavlodar': r'павлодар|pavlodar',
    'taraz': r'тараз|taraz',
    'ekibastuz': r'экибастуз\w*|екібастұз\w*|ekibastuz',
}


def _secondary_city_confirmed(item: SearchItem) -> bool:
    """Avoid treating a city-specific web query as proof of a result's location."""
    text = f'{item.title or ""} {item.snippet or ""}'.casefold()
    observed = {
        city for city, words in _CITY_WORDS.items()
        if re.search(rf'(?<!\w)(?:{words})(?!\w)(?!\s+(?:район|р-н|р\.\s*н))', text)
    }
    return observed == {settings.search_city}


async def _save_items(
    provider_name: str,
    query: str,
    items: list[SearchItem],
) -> tuple[int, int, list[SearchImportCandidate]]:
    accepted_by_url: dict[str, SearchItem] = {}
    for item in items:
        if settings.market_type == 'new' and provider_name not in QUERYLESS_PROVIDERS:
            continue  # Web snippets cannot establish a primary-market listing.
        if _matches_target(item) and (provider_name not in {'brave', 'bing_rss'} or _secondary_city_confirmed(item)):
            accepted_by_url[item.url] = item
    accepted_items = list(accepted_by_url.values())
    accepted_count = len(accepted_items)
    new_found = 0
    candidates: list[SearchImportCandidate] = []
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        known_pledges: dict[str, bool] = {}
        known_cities: dict[str, set[KnownCity | None]] = {}
        listing_urls: dict[str, list[str]] = {}
        known_markets: dict[str, set[str]] = {}
        if accepted_items:
            for url, city, pledged, saved_market in (await session.execute(
                select(Listing.source_url, Listing.city, Listing.is_pledged, Listing.market_type)
            )).all():
                key = olx.olx_advert_id(url) or canonical_url(url)
                listing_urls.setdefault(key, []).append(url)
                if saved_market in {'new', 'secondary'}:
                    known_markets.setdefault(key, set()).add(saved_market)
                if city and city.strip():
                    known_cities.setdefault(key, set()).add(city_slug(city))
                if pledged is not None:
                    known_pledges[key] = pledged
        for item in accepted_items:
            existing = await session.scalar(select(SearchResult).where(SearchResult.url == item.url))
            olx_id = olx.olx_advert_id(item.url)
            if existing is None and olx_id:
                variants = await session.scalars(select(SearchResult).where(
                    SearchResult.url.contains(f"{olx_id}.html", autoescape=True)).order_by(SearchResult.id))
                existing = next((row for row in variants if olx.olx_advert_id(row.url) == olx_id), None)
            krisha_url = normalize_krisha_listing_url(item.url) if provider_name == "krisha_direct" else None
            canonical = olx_id or canonical_url(item.url)
            known = known_cities.get(canonical)
            if known and known != {settings.search_city}:
                accepted_count -= 1
                continue
            if (existing and existing.city and city_slug(existing.city) != settings.search_city
                    and provider_name != 'krisha_direct'):
                accepted_count -= 1
                continue
            direct_source = bool(krisha_url or olx_id)
            seller_type = item.seller_type if direct_source and seller_category(item.seller_type) != "unknown" else None
            market_type = item.market_type if direct_source and item.market_type in {'new', 'secondary'} else None
            if krisha_url and seller_type:
                await fill_seller_from_krisha_search(session, krisha_url, seller_type)
            if market_type and canonical in listing_urls:
                await session.execute(update(Listing).where(
                    Listing.source_url.in_(listing_urls[canonical]), Listing.market_type.is_(None)
                ).values(market_type=market_type, updated_at=Listing.updated_at))
            known_types = known_markets.get(canonical, set())
            discovery_market = market_type or getattr(existing, 'market_type', None)
            effective_market = ((next(iter(known_types)) if len(known_types) == 1 else None)
                                if known_types else discovery_market)
            included_market = matches_market_type(settings.market_type, effective_market)
            is_pledged = known_pledges.get(canonical)
            bathrooms_count = parse_bathrooms_count(f"{item.title or ''} {item.snippet or ''}")
            mortgage_supported = parse_mortgage_text(f"{item.title or ''}\n{item.snippet or ''}")
            lower_bound = bathrooms_is_lower_bound(f"{item.title or ''} {item.snippet or ''}")
            if item.payload is not None:
                if item.payload.bathrooms_count is not None:
                    bathrooms_count = item.payload.bathrooms_count
                    lower_bound = item.payload.bathrooms_count_is_lower_bound
                if item.payload.mortgage_supported is not None:
                    mortgage_supported = item.payload.mortgage_supported
                if item.payload.is_pledged is not None:
                    is_pledged = item.payload.is_pledged
            if existing:
                existing.city = settings.search_city
                existing.last_seen = now
                if direct_source:
                    existing.market_type = market_type
                if seller_type:
                    existing.seller_type = seller_type
                listing = (await session.get(Listing, existing.imported_listing_id)
                           if existing.imported_listing_id is not None else None)
                if listing is not None:
                    sync_search_result_facts(existing, listing)
                else:
                    existing.title = item.title or existing.title
                    existing.snippet = item.snippet or existing.snippet
                    if is_pledged is not None:
                        existing.is_pledged = is_pledged
                    if mortgage_supported is not None:
                        existing.mortgage_supported = mortgage_supported
                    if bathrooms_count is not None:
                        existing.bathrooms_count = bathrooms_count
                        existing.bathrooms_count_is_lower_bound = lower_bound
                if _row_hidden_by_pledge(existing):
                    accepted_count -= 1
                    continue
                if not included_market:
                    accepted_count -= 1
                    continue
                if existing.import_status != "imported" or existing.imported_listing_id is None:
                    candidates.append(SearchImportCandidate(existing.id, item.url, settings.search_city, item.payload))
                continue
            row = SearchResult(
                url=item.url,
                city=settings.search_city,
                search_engine=provider_name,
                query=query,
                title=item.title,
                snippet=item.snippet,
                seller_type=seller_type,
                market_type=market_type,
                bathrooms_count=bathrooms_count,
                mortgage_supported=mortgage_supported,
                is_pledged=is_pledged,
                bathrooms_count_is_lower_bound=lower_bound,
                status=f"new:{_priority(item.title, item.snippet, mortgage_supported, is_pledged)}",
            )
            session.add(row)
            await session.flush()
            if _row_hidden_by_pledge(row):
                accepted_count -= 1
                continue
            if not included_market:
                accepted_count -= 1
                continue
            candidates.append(SearchImportCandidate(row.id, row.url, settings.search_city, item.payload))
            new_found += 1
        session.add(
            SearchHistory(
                search_engine=provider_name,
                city=settings.search_city,
                query=query,
                total_found=accepted_count,
                new_found=new_found,
                status="ok",
                message=(
                    f"Отфильтровано в текущей выдаче: {len(items) - accepted_count}; "
                    f"принято: {accepted_count}"
                ),
            )
        )
        await session.commit()
    return new_found, accepted_count, candidates


async def _mark_import_result(
    candidate: SearchImportCandidate,
    *,
    import_status: str,
    error: str | None = None,
    listing_id: int | None = None,
) -> None:
    async with SessionLocal() as session:
        row = await session.get(SearchResult, candidate.result_id)
        if row is None:
            return
        if import_status in {"archived", "unavailable"}:
            await set_source_availability(session, row.url, state=import_status)
        row.import_status = import_status
        row.import_error = error[:500] if error else None
        if listing_id is not None:
            row.imported_listing_id = listing_id
            listing = await session.get(Listing, listing_id)
            if listing is not None and listing.bathrooms_count is not None:
                row.bathrooms_count = listing.bathrooms_count
                row.bathrooms_count_is_lower_bound = listing.bathrooms_count_is_lower_bound
        if import_status == "imported":
            row.imported_at = datetime.now(UTC)
        await session.commit()


async def _auto_import(candidates: list[SearchImportCandidate]) -> tuple[int, int, int]:
    if not settings.auto_import_enabled:
        return 0, 0, 0

    imported = errors = blocked = 0
    blocked_sources: dict[str, str] = {}
    unique_candidates = list({candidate.url: candidate for candidate in candidates}.values())
    for candidate in unique_candidates:
        is_olx = olx.olx_advert_id(candidate.url) is not None
        source = "olx" if is_olx else "krisha"
        if source in blocked_sources:
            await _mark_import_result(
                candidate,
                import_status="blocked",
                error=blocked_sources[source],
            )
            blocked += 1
            continue
        try:
            if is_olx:
                payload = candidate.payload
                if payload is None or not payload.details_extracted:
                    payload = await olx.import_olx_listing(candidate.url, expected_city=candidate.city)
            else:
                payload = (await import_krisha_listing(candidate.url) if candidate.city == "astana" else
                           await import_krisha_listing(candidate.url, expected_city=candidate.city))
            if city_slug(payload.city) != candidate.city:
                raise ValueError("Город объявления не совпадает с выбранным городом поиска")
            if is_olx and settings.exclude_pledged and payload.is_pledged is True:
                async with SessionLocal() as session:
                    row = await session.get(SearchResult, candidate.result_id)
                    if row is not None:
                        row.is_pledged = True
                        await session.commit()
                continue
            actual_market = getattr(payload, 'market_type', None)
            if (is_olx or actual_market in ('new', 'secondary')) and not matches_market_type(settings.market_type, actual_market):
                async with SessionLocal() as session:
                    row = await session.get(SearchResult, candidate.result_id)
                    if row is not None:
                        row.market_type = actual_market
                        row.import_status = 'market_mismatch'
                        row.import_error = 'Тип рынка объявления не соответствует выбранному фильтру'
                        await session.commit()
                errors += 1
                continue
            saved = await persist_listing(payload, allow_existing=True, notify=True)
            await _mark_import_result(
                candidate,
                import_status="imported",
                listing_id=saved.row.id,
            )
            imported += 1
        except (ListingArchived, ListingUnavailable) as exc:
            state = "archived" if isinstance(exc, ListingArchived) else "unavailable"
            await _mark_import_result(candidate, import_status=state, error=str(exc))
        except ListingImportError as exc:
            message = str(exc)
            is_blocked = any(word in message.lower() for word in ("огранич", "провер", "captcha", "403", "429"))
            if is_blocked:
                blocked_sources[source] = message
                blocked += 1
                await _mark_import_result(candidate, import_status="blocked", error=message)
            else:
                errors += 1
                await _mark_import_result(candidate, import_status="error", error=message)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            await _mark_import_result(candidate, import_status="error", error=str(exc))
        if settings.auto_import_delay_seconds > 0 and not (is_olx and candidate.payload and candidate.payload.details_extracted):
            await asyncio.sleep(settings.auto_import_delay_seconds)
    return imported, errors, blocked


async def _save_provider_status(
    provider_name: str,
    query: str,
    status: str,
    message: str,
) -> None:
    async with SessionLocal() as session:
        session.add(
            SearchHistory(
                city=settings.search_city,
                search_engine=provider_name,
                query=query,
                total_found=0,
                new_found=0,
                status=status,
                message=message[:500],
            )
        )
        await session.commit()


async def _fetch_one(
    provider_name: str,
    query: str,
) -> tuple[str, str, list[SearchItem] | None, Exception | None]:
    try:
        timeout = 75.0 if provider_name in QUERYLESS_PROVIDERS else 25.0
        items = await asyncio.wait_for(PROVIDERS[provider_name](query), timeout=timeout)
        return provider_name, query, items, None
    except Exception as exc:  # noqa: BLE001
        return provider_name, query, None, exc


async def run_search(rooms: int | None = None, max_price_kzt: int | None = None,
                     min_area_m2: float | None = None, max_area_m2: float | None = None,
                     min_bathrooms: int | None = None, exclude_pledged: bool | None = None,
                     *, max_area_m2_is_set: bool = False, seller: str | None = None,
                     min_price_kzt: int | None = None, min_price_kzt_is_set: bool = False,
                     city: SearchCity | None = None, conditions: dict | None = None,
                     market_type: MarketFilter | None = None) -> SearchSummary:
    async with _search_lock:
        if max_area_m2_is_set or min_price_kzt_is_set or any(value is not None for value in (rooms, min_price_kzt, max_price_kzt, min_area_m2, max_area_m2, min_bathrooms, exclude_pledged, city, conditions, market_type)):
            await save_search_preference(rooms, max_price_kzt, min_area_m2, max_area_m2, min_bathrooms, exclude_pledged,
                                         max_area_m2_is_set=max_area_m2_is_set, min_price_kzt=min_price_kzt,
                                         min_price_kzt_is_set=min_price_kzt_is_set, city=city, conditions=conditions, market_type=market_type)
        return await _run_search(seller=seller)


async def _run_search(*, seller: str | None = None) -> SearchSummary:
    summary = SearchSummary()
    if not settings.search_enabled:
        return summary

    queries = _rotated_queries()
    owner_search = seller == 'owner'
    krisha_owner_search = owner_search or settings.market_type == 'secondary'
    direct_providers = ["krisha_direct"] + (["olx_direct"] if "olx_direct" in settings.search_providers else [])
    enabled_providers = (direct_providers if krisha_owner_search else
                         [name for name in settings.search_providers if name in PROVIDERS])
    jobs: list[tuple[str, str]] = []

    for provider_name in enabled_providers:
        owner_only = krisha_owner_search if provider_name == "krisha_direct" else owner_search
        provider_queries = [f"owner:{settings.target_rooms}" if owner_only else str(settings.target_rooms)] if provider_name in QUERYLESS_PROVIDERS else queries
        summary.providers[provider_name] = {
            "queries": len(provider_queries),
            "found": 0,
            "new": 0,
            "errors": 0,
            "blocked": 0,
            "imported": 0,
            "import_errors": 0,
            "import_blocked": 0,
        }
        jobs.extend((provider_name, query) for query in provider_queries)

    summary.queries = len(jobs)
    results = await asyncio.gather(*(_fetch_one(provider_name, query) for provider_name, query in jobs))

    for provider_name, query, items, error in results:
        stats = summary.providers[provider_name]
        if error is not None:
            if isinstance(error, (KrishaSearchBlocked, olx.OlxSearchBlocked)):
                summary.blocked += 1
                stats["blocked"] += 1
                await _save_provider_status(provider_name, query, "blocked", str(error))
            else:
                summary.errors += 1
                stats["errors"] += 1
                await _save_provider_status(provider_name, query, "error", str(error))
            continue

        safe_items = items or []
        if owner_search or (provider_name == "krisha_direct" and krisha_owner_search):
            safe_items = [item for item in safe_items if seller_category(item.seller_type) == "owner"]
        try:
            new_found, accepted_count, candidates = await _save_items(provider_name, query, safe_items)
            imported, import_errors, import_blocked = await _auto_import(candidates)
        except Exception as exc:  # noqa: BLE001
            summary.errors += 1
            stats["errors"] += 1
            await _save_provider_status(provider_name, query, "error", str(exc))
            continue

        summary.found += accepted_count
        summary.new += new_found
        stats["found"] += accepted_count
        stats["new"] += new_found
        stats["imported"] += imported
        stats["import_errors"] += import_errors
        stats["import_blocked"] += import_blocked
        summary.imported += imported
        summary.import_errors += import_errors
        summary.import_blocked += import_blocked

    return summary


async def periodic_search(stop_event: asyncio.Event) -> None:
    interval_seconds = max(settings.search_interval_minutes, 15) * 60
    while not stop_event.is_set():
        try:
            async with SessionLocal() as session:
                preference = await session.get(SearchPreference, 1)
                active_id = preference.active_profile_id if preference else None
                active = await session.get(SearchProfile, active_id) if active_id else None
                active_city = (active.criteria or {}).get('city', 'astana') if active else None
                run_active = (active is None or active_city not in SEARCH_CITIES or active.enabled)
                enabled_profiles = (await session.scalars(select(SearchProfile).where(SearchProfile.enabled.is_(True)))).all()
                enabled_astana = any((row.criteria or {}).get('city', 'astana') == 'astana' for row in enabled_profiles)
            if settings.search_city == 'astana' and run_active:
                await run_search()
            elif settings.search_city == 'ekibastuz' and not enabled_astana:
                # Manual Ekibastuz search must not replace background Astana.
                override = get_settings().model_copy(deep=True, update={
                    'search_city': 'astana', 'auto_import_enabled': False,
                })
                async with _search_lock:
                    token = search_settings_override.set(override)
                    try:
                        await _run_search()
                    finally:
                        search_settings_override.reset(token)
            await run_monitored_profiles()
        except Exception:
            logger.exception("Periodic search failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


async def run_monitored_profiles() -> None:
    from app.search_profiles import ProfileCriteria
    async with SessionLocal() as session:
        rows = (await session.scalars(select(SearchProfile).where(SearchProfile.enabled.is_(True)))).all()
        preference = await session.get(SearchPreference, 1)
        active_id = preference.active_profile_id if preference else None
        profiles = [(row.id, row.criteria) for row in rows
                    if row.id != active_id and (row.criteria or {}).get('city', 'astana') == 'astana']
    for profile_id, raw in profiles:
        try:
            criteria = ProfileCriteria.model_validate(raw)
            override = get_settings().model_copy(deep=True, update={
                'search_city': criteria.city, 'target_rooms': criteria.rooms,
                'min_price_kzt': criteria.min_price_kzt, 'max_price_kzt': criteria.max_price_kzt,
                'min_area_m2': criteria.min_area_m2, 'max_area_m2': criteria.max_area_m2,
                'min_bathrooms': criteria.min_bathrooms, 'exclude_pledged': criteria.exclude_pledged,
                'search_conditions': criteria.conditions.model_dump(),
                'market_type': criteria.market_type,
                # Import/notifications use active UI criteria; defer import until
                # the profile is active rather than mixing two configurations.
                'auto_import_enabled': False,
            })
            async with _search_lock:
                token = search_settings_override.set(override)
                try:
                    await _run_search()
                finally:
                    search_settings_override.reset(token)
        except Exception:
            logger.exception('Monitored search profile %s failed', profile_id)


async def get_search_status() -> dict[str, object]:
    now = datetime.now(UTC)
    hour_ago = now - timedelta(hours=1)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with SessionLocal() as session:
        unavailable = await unavailable_urls(session)
        total = new_total = imported_total = found_hour = found_today = urgent = bathrooms_unconfirmed = 0
        pledged_hidden = 0
        engine_counts: dict[str, int] = {}
        location_rows = (await session.execute(
                select(Listing.id, Listing.city, Listing.latitude, Listing.longitude, Listing.floor,
                       Listing.floors_total, Listing.building_year, Listing.property_details, Listing.market_type)
            )).all()
        locations = {value.id: listing_river_bank(value.city, value.latitude, value.longitude) for value in location_rows}
        linked = {value.id: value for value in location_rows}
        matching_rows = await session.stream_scalars(select(SearchResult))
        async for row in matching_rows:
            card = linked.get(getattr(row, "imported_listing_id", None))
            if not city_matches(settings.search_city, getattr(row, "city", None), card.city if card else None):
                continue
            if not matches_market_type(settings.market_type, (card.market_type if card and card.market_type else getattr(row, 'market_type', None))):
                continue
            if not conditions_for_result(row, card)['matches_hard']:
                continue
            if availability_key(row.url) in unavailable:
                continue
            if _row_hidden_by_pledge(row):
                pledged_hidden += int(_row_matches_basic(row, include_pledged=True))
                continue
            if settings.min_bathrooms and _row_matches_basic(row) and bathrooms_for_row(row) is None:
                bathrooms_unconfirmed += 1
            if not _row_matches_target(row):
                continue
            total += 1
            is_new = row.status.startswith("new:")
            new_total += int(is_new)
            imported_total += int(row.import_status == "imported")
            first_seen = row.first_seen if row.first_seen.tzinfo else row.first_seen.replace(tzinfo=UTC)
            found_hour += int(first_seen >= hour_ago)
            found_today += int(first_seen >= day_start)
            urgent += int(is_new and _priority(row.title or "", row.snippet, getattr(row, "mortgage_supported", None),
                                              getattr(row, "is_pledged", None),
                                              locations.get(getattr(row, "imported_listing_id", None), "unknown")) == "urgent")
            engine_counts[row.search_engine] = engine_counts.get(row.search_engine, 0) + 1
        history_filter = ((SearchHistory.city == "astana") | SearchHistory.city.is_(None)) if settings.search_city == "astana" else SearchHistory.city == settings.search_city
        last_run = await session.scalar(select(func.max(SearchHistory.searched_at)).where(history_filter))
        latest_history = (
            await session.scalars(
                select(SearchHistory).where(history_filter).order_by(SearchHistory.searched_at.desc()).limit(100)
            )
        ).all()

    provider_status: dict[str, dict[str, object]] = {}
    for row in latest_history:
        if row.search_engine in provider_status:
            continue
        provider_status[row.search_engine] = {
            "status": row.status,
            "message": row.message,
            "searched_at": row.searched_at,
            "total_found": row.total_found,
            "new_found": row.new_found,
        }

    if "brave" in settings.search_providers and not settings.brave_search_api_key:
        provider_status["brave"] = {
            "status": "not_configured",
            "message": "BRAVE_SEARCH_API_KEY не задан",
            "searched_at": None,
            "total_found": 0,
            "new_found": 0,
        }

    return {
        "city": settings.search_city,
        "market_type": settings.market_type,
        "conditions": settings.search_conditions,
        "rooms": settings.target_rooms,
        "min_price_kzt": settings.min_price_kzt,
        "max_price_kzt": settings.max_price_kzt,
        "min_area_m2": settings.min_area_m2,
        "max_area_m2": settings.max_area_m2,
        "min_bathrooms": settings.min_bathrooms,
        "exclude_pledged": settings.exclude_pledged,
        "pledged_hidden_results": pledged_hidden,
        "bathrooms_unconfirmed_results": bathrooms_unconfirmed,
        "enabled": settings.search_enabled,
        "providers": settings.search_providers,
        "provider_status": provider_status,
        "brave_configured": bool(settings.brave_search_api_key),
        "krisha_direct_configured": "krisha_direct" in settings.search_providers,
        "interval_minutes": max(settings.search_interval_minutes, 15),
        "queries_per_run": settings.search_queries_per_run,
        "total_results": total,
        "new_results": new_total,
        "imported_results": imported_total,
        "auto_import_enabled": settings.auto_import_enabled,
        "urgent_results": urgent,
        "found_last_hour": found_hour,
        "found_today": found_today,
        "last_run": last_run,
        "by_provider": engine_counts,
    }


async def load_search_preference() -> None:
    async with SessionLocal() as session:
        preference = await session.get(SearchPreference, 1)
        settings.market_type = preference.market_type if preference and preference.market_type in ('all', 'new', 'secondary') else 'all'
        if preference and preference.conditions is not None:
            settings.search_conditions = SearchConditions.model_validate(preference.conditions).model_dump()
        selected = city_slug(preference.city) if preference else None
        settings.search_city = selected if selected in SEARCH_CITIES else 'astana'
        if preference and preference.rooms in (2, 3, 4):
            settings.target_rooms = preference.rooms
        if preference and preference.max_price_kzt is not None:
            settings.min_price_kzt = preference.min_price_kzt
            settings.max_price_kzt = preference.max_price_kzt
        # A saved minimum distinguishes a configured range from a legacy row
        # without an area preference. Its maximum can deliberately be null.
        if preference and preference.min_area_m2 is not None:
            settings.min_area_m2 = preference.min_area_m2
            settings.max_area_m2 = preference.max_area_m2
        if preference and preference.min_bathrooms in (0, 2):
            settings.min_bathrooms = preference.min_bathrooms
        if preference and preference.exclude_pledged is not None:
            settings.exclude_pledged = preference.exclude_pledged


async def save_search_preference(rooms: int | None, max_price_kzt: int | None = None,
                                 min_area_m2: float | None = None, max_area_m2: float | None = None,
                                 min_bathrooms: int | None = None, exclude_pledged: bool | None = None,
                                 *, max_area_m2_is_set: bool = False,
                                 min_price_kzt: int | None = None, min_price_kzt_is_set: bool = False,
                                 city: SearchCity | None = None, conditions: dict | None = None,
                                 active_profile_id: int | None = None, market_type: MarketFilter | None = None) -> None:
    if market_type is not None and market_type not in ('all', 'new', 'secondary'):
        raise ValueError('Неизвестный тип рынка')
    validated_conditions = SearchConditions.model_validate(conditions).model_dump() if conditions is not None else None
    if city is not None and city not in SEARCH_CITIES:
        raise ValueError("Неизвестный город поиска")
    update_min_price = min_price_kzt_is_set or min_price_kzt is not None
    update_price = update_min_price or max_price_kzt is not None
    min_price = min_price_kzt if update_min_price else settings.min_price_kzt
    max_price = settings.max_price_kzt if max_price_kzt is None else max_price_kzt
    if update_price and min_price is not None and min_price > max_price:
        raise InvalidSearchPrice("Минимальная цена не может превышать максимальную")
    update_max_area = max_area_m2_is_set or max_area_m2 is not None
    update_area = min_area_m2 is not None or update_max_area
    min_area = settings.min_area_m2 if min_area_m2 is None else min_area_m2
    max_area = max_area_m2 if update_max_area else settings.max_area_m2
    if update_area and max_area is not None and min_area > max_area:
        raise InvalidSearchArea("Минимальная площадь не может превышать максимальную")

    async with SessionLocal() as session:
        preference = await session.get(SearchPreference, 1)
        if preference is None:
            preference = SearchPreference(id=1, rooms=rooms or settings.target_rooms)
            session.add(preference)
        elif rooms is not None:
            preference.rooms = rooms
        if update_price:
            preference.min_price_kzt = min_price
            preference.max_price_kzt = max_price
        if update_area:
            preference.min_area_m2 = min_area
            preference.max_area_m2 = max_area
        if min_bathrooms is not None:
            preference.min_bathrooms = min_bathrooms
        if exclude_pledged is not None:
            preference.exclude_pledged = exclude_pledged
        if city is not None:
            preference.city = city
        if validated_conditions is not None:
            preference.conditions = validated_conditions
        if market_type is not None:
            preference.market_type = market_type
        preference.active_profile_id = active_profile_id
        await session.commit()
    if city is not None:
        settings.search_city = city
    if market_type is not None:
        settings.market_type = market_type
    if validated_conditions is not None:
        settings.search_conditions = validated_conditions
    if rooms is not None:
        settings.target_rooms = rooms
    if update_price:
        settings.min_price_kzt = min_price
        settings.max_price_kzt = max_price
    if min_bathrooms is not None:
        settings.min_bathrooms = min_bathrooms
    if exclude_pledged is not None:
        settings.exclude_pledged = exclude_pledged
    if update_area:
        settings.min_area_m2 = min_area
        settings.max_area_m2 = max_area
