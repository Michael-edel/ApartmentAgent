import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.importer import ListingImportError, import_krisha_listing
from app.krisha_search import KrishaSearchBlocked
from app.listing_service import persist_listing
from app.models import Listing, SearchHistory, SearchPreference, SearchResult
from app.mortgage import parse_mortgage_text
from app.search_analysis import analyze_search_result, is_target_search_result, parse_bathrooms_count, bathrooms_is_lower_bound
from app.search_providers import PROVIDERS, SearchItem

settings = get_settings()
logger = logging.getLogger(__name__)
_search_lock = asyncio.Lock()

DISTRICTS = ["Есильский район", "Нура район", "Алматы район", "Сарыарка район", "Байконур район"]
QUERYLESS_PROVIDERS = {"krisha_direct"}


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


def _priority(title: str, snippet: str | None, mortgage_supported: bool | None = None) -> str:
    result = analyze_search_result(
        title,
        snippet,
        max_price=settings.max_price_kzt,
        min_area=settings.min_area_m2,
        max_area=settings.max_area_m2,
        target_rooms=settings.target_rooms,
        mortgage_supported=mortgage_supported,
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
        min_area=settings.min_area_m2,
        max_area=settings.max_area_m2,
        target_rooms=settings.target_rooms,
    )


def bathrooms_for_row(row: SearchResult) -> int | None:
    count = getattr(row, 'bathrooms_count', None)
    return count if count is not None else parse_bathrooms_count(f"{row.title or ''} {row.snippet or ''}")


def _row_matches_target(row: SearchResult) -> bool:
    bathrooms = bathrooms_for_row(row)
    return _row_matches_basic(row) and (not settings.min_bathrooms or (
        bathrooms is not None and bathrooms >= settings.min_bathrooms
    ))


def _row_matches_basic(row: SearchResult) -> bool:
    return is_target_search_result(
        row.title,
        row.snippet,
        max_price=settings.max_price_kzt,
        min_area=settings.min_area_m2,
        max_area=settings.max_area_m2,
        target_rooms=settings.target_rooms,
    )


def _rotated_queries() -> list[str]:
    rooms = settings.target_rooms
    queries = [f"Астана {rooms}-комнатная квартира продажа krisha.kz"] + [f"Астана {rooms}-комнатная квартира {district} krisha.kz" for district in DISTRICTS]
    count = min(max(settings.search_queries_per_run, 1), len(queries))
    slot = int(datetime.now(UTC).timestamp() // (max(settings.search_interval_minutes, 15) * 60))
    start = (slot * count) % len(queries)
    return [queries[(start + offset) % len(queries)] for offset in range(count)]


async def _save_items(
    provider_name: str,
    query: str,
    items: list[SearchItem],
) -> tuple[int, int, list[SearchImportCandidate]]:
    accepted_by_url: dict[str, SearchItem] = {}
    for item in items:
        if _matches_target(item):
            accepted_by_url[item.url] = item
    accepted_items = list(accepted_by_url.values())
    new_found = 0
    candidates: list[SearchImportCandidate] = []
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        for item in accepted_items:
            existing = await session.scalar(select(SearchResult).where(SearchResult.url == item.url))
            bathrooms_count = parse_bathrooms_count(f"{item.title or ''} {item.snippet or ''}")
            mortgage_supported = parse_mortgage_text(f"{item.title or ''} {item.snippet or ''}")
            if existing:
                existing.last_seen = now
                existing.title = item.title or existing.title
                existing.snippet = item.snippet or existing.snippet
                if mortgage_supported is not None:
                    existing.mortgage_supported = mortgage_supported
                if bathrooms_count is not None:
                    existing.bathrooms_count = bathrooms_count
                    existing.bathrooms_count_is_lower_bound = bathrooms_is_lower_bound(f"{item.title or ''} {item.snippet or ''}")
                if existing.import_status != "imported" or existing.imported_listing_id is None:
                    candidates.append(SearchImportCandidate(result_id=existing.id, url=existing.url))
                continue
            row = SearchResult(
                url=item.url,
                search_engine=provider_name,
                query=query,
                title=item.title,
                snippet=item.snippet,
                bathrooms_count=bathrooms_count,
                mortgage_supported=mortgage_supported,
                bathrooms_count_is_lower_bound=bathrooms_is_lower_bound(f"{item.title or ''} {item.snippet or ''}"),
                status=f"new:{_priority(item.title, item.snippet)}",
            )
            session.add(row)
            await session.flush()
            candidates.append(SearchImportCandidate(result_id=row.id, url=row.url))
            new_found += 1
        session.add(
            SearchHistory(
                search_engine=provider_name,
                query=query,
                total_found=len(accepted_items),
                new_found=new_found,
                status="ok",
                message=(
                    f"Отфильтровано в текущей выдаче: {len(items) - len(accepted_items)}; "
                    f"принято: {len(accepted_items)}"
                ),
            )
        )
        await session.commit()
    return new_found, len(accepted_items), candidates


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
        row.import_status = import_status
        row.import_error = error[:500] if error else None
        row.imported_listing_id = listing_id
        if listing_id is not None:
            listing = await session.get(Listing, listing_id)
            if listing is not None and listing.bathrooms_count is not None:
                row.bathrooms_count = listing.bathrooms_count
                row.bathrooms_count_is_lower_bound = listing.bathrooms_count_is_lower_bound
        row.imported_at = datetime.now(UTC) if import_status == "imported" else None
        await session.commit()


async def _auto_import(candidates: list[SearchImportCandidate]) -> tuple[int, int, int]:
    if not settings.auto_import_enabled:
        return 0, 0, 0

    imported = errors = blocked = 0
    blocked_message: str | None = None
    unique_candidates = list({candidate.url: candidate for candidate in candidates}.values())
    for candidate in unique_candidates:
        if blocked_message:
            await _mark_import_result(
                candidate,
                import_status="blocked",
                error=blocked_message,
            )
            blocked += 1
            continue
        try:
            payload = await import_krisha_listing(candidate.url)
            saved = await persist_listing(payload, allow_existing=True, notify=True)
            await _mark_import_result(
                candidate,
                import_status="imported",
                listing_id=saved.row.id,
            )
            imported += 1
        except ListingImportError as exc:
            message = str(exc)
            is_blocked = any(word in message.lower() for word in ("огранич", "провер", "captcha", "403", "429"))
            if is_blocked:
                blocked_message = message
                blocked += 1
                await _mark_import_result(candidate, import_status="blocked", error=message)
            else:
                errors += 1
                await _mark_import_result(candidate, import_status="error", error=message)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            await _mark_import_result(candidate, import_status="error", error=str(exc))
        if settings.auto_import_delay_seconds > 0:
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
        timeout = 75.0 if provider_name == "krisha_direct" else 25.0
        items = await asyncio.wait_for(PROVIDERS[provider_name](query), timeout=timeout)
        return provider_name, query, items, None
    except Exception as exc:  # noqa: BLE001
        return provider_name, query, None, exc


async def run_search(rooms: int | None = None, max_price_kzt: int | None = None,
                     min_area_m2: float | None = None, max_area_m2: float | None = None,
                     min_bathrooms: int | None = None) -> SearchSummary:
    async with _search_lock:
        if rooms is not None or max_price_kzt is not None or min_area_m2 is not None or max_area_m2 is not None or min_bathrooms is not None:
            await save_search_preference(rooms, max_price_kzt, min_area_m2, max_area_m2, min_bathrooms)
        return await _run_search()


async def _run_search() -> SearchSummary:
    summary = SearchSummary()
    if not settings.search_enabled:
        return summary

    queries = _rotated_queries()
    enabled_providers = [name for name in settings.search_providers if name in PROVIDERS]
    jobs: list[tuple[str, str]] = []

    for provider_name in enabled_providers:
        provider_queries = [str(settings.target_rooms)] if provider_name in QUERYLESS_PROVIDERS else queries
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
            if isinstance(error, KrishaSearchBlocked):
                summary.blocked += 1
                stats["blocked"] += 1
                await _save_provider_status(provider_name, query, "blocked", str(error))
            else:
                summary.errors += 1
                stats["errors"] += 1
                await _save_provider_status(provider_name, query, "error", str(error))
            continue

        safe_items = items or []
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
            await run_search()
        except Exception:
            logger.exception("Periodic search failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


async def get_search_status() -> dict[str, object]:
    now = datetime.now(UTC)
    hour_ago = now - timedelta(hours=1)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with SessionLocal() as session:
        total = new_total = imported_total = found_hour = found_today = urgent = bathrooms_unconfirmed = 0
        engine_counts: dict[str, int] = {}
        matching_rows = await session.stream_scalars(select(SearchResult))
        async for row in matching_rows:
            if settings.min_bathrooms and _row_matches_basic(row) and bathrooms_for_row(row) is None:
                bathrooms_unconfirmed += 1
            if not _row_matches_target(row):
                continue
            total += 1
            is_new = row.status.startswith("new:")
            new_total += int(is_new)
            imported_total += int(row.import_status == "imported")
            found_hour += int(row.first_seen >= hour_ago)
            found_today += int(row.first_seen >= day_start)
            urgent += int(is_new and _priority(row.title or "", row.snippet, getattr(row, 'mortgage_supported', None)) == "urgent")
            engine_counts[row.search_engine] = engine_counts.get(row.search_engine, 0) + 1
        last_run = await session.scalar(select(func.max(SearchHistory.searched_at)))
        latest_history = (
            await session.scalars(
                select(SearchHistory).order_by(SearchHistory.searched_at.desc()).limit(100)
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
        "rooms": settings.target_rooms,
        "max_price_kzt": settings.max_price_kzt,
        "min_area_m2": settings.min_area_m2,
        "max_area_m2": settings.max_area_m2,
        "min_bathrooms": settings.min_bathrooms,
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
        if preference and preference.rooms in (2, 3, 4):
            settings.target_rooms = preference.rooms
        if preference and preference.max_price_kzt is not None:
            settings.max_price_kzt = preference.max_price_kzt
        if preference and preference.min_area_m2 is not None and preference.max_area_m2 is not None:
            settings.min_area_m2 = preference.min_area_m2
            settings.max_area_m2 = preference.max_area_m2
        if preference and preference.min_bathrooms in (0, 2):
            settings.min_bathrooms = preference.min_bathrooms


async def save_search_preference(rooms: int | None, max_price_kzt: int | None = None,
                                 min_area_m2: float | None = None, max_area_m2: float | None = None,
                                 min_bathrooms: int | None = None) -> None:
    async with SessionLocal() as session:
        preference = await session.get(SearchPreference, 1)
        if preference is None:
            preference = SearchPreference(id=1, rooms=rooms or settings.target_rooms)
            session.add(preference)
        elif rooms is not None:
            preference.rooms = rooms
        if max_price_kzt is not None:
            preference.max_price_kzt = max_price_kzt
        if min_area_m2 is not None and max_area_m2 is not None:
            preference.min_area_m2 = min_area_m2
            preference.max_area_m2 = max_area_m2
        if min_bathrooms is not None:
            preference.min_bathrooms = min_bathrooms
        await session.commit()
    if rooms is not None:
        settings.target_rooms = rooms
    if max_price_kzt is not None:
        settings.max_price_kzt = max_price_kzt
    if min_bathrooms is not None:
        settings.min_bathrooms = min_bathrooms
    if min_area_m2 is not None and max_area_m2 is not None:
        settings.min_area_m2 = min_area_m2
        settings.max_area_m2 = max_area_m2
