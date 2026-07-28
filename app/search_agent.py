import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.krisha_search import KrishaSearchBlocked
from app.models import SearchHistory, SearchResult
from app.search_analysis import analyze_search_result, is_target_search_result
from app.search_providers import PROVIDERS, SearchItem

settings = get_settings()

DISTRICTS = ["Есильский район", "Нура район", "Алматы район", "Сарыарка район", "Байконур район"]
BASE_QUERIES = [
    "Астана купить 2-комнатную квартиру krisha.kz",
    "Астана 2-комнатная квартира продажа krisha.kz",
    "Астана двухкомнатная квартира krisha.kz",
    "Астана полноценная 2-комнатная квартира krisha.kz",
    "Астана квартира 2 комнаты до 30 млн krisha.kz",
]
SEARCH_QUERIES = BASE_QUERIES + [
    f"Астана 2-комнатная квартира {district} krisha.kz" for district in DISTRICTS
]
QUERYLESS_PROVIDERS = {"krisha_direct"}


@dataclass(slots=True)
class SearchSummary:
    queries: int = 0
    found: int = 0
    new: int = 0
    errors: int = 0
    blocked: int = 0
    providers: dict[str, dict[str, int]] = field(default_factory=dict)


def _priority(title: str, snippet: str | None) -> str:
    result = analyze_search_result(
        title,
        snippet,
        max_price=settings.max_price_kzt,
        min_area=settings.min_area_m2,
        max_area=settings.max_area_m2,
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
    )


def _rotated_queries() -> list[str]:
    count = min(max(settings.search_queries_per_run, 1), len(SEARCH_QUERIES))
    slot = int(datetime.now(timezone.utc).timestamp() // (max(settings.search_interval_minutes, 15) * 60))
    start = (slot * count) % len(SEARCH_QUERIES)
    return [SEARCH_QUERIES[(start + offset) % len(SEARCH_QUERIES)] for offset in range(count)]


async def _save_items(provider_name: str, query: str, items: list[SearchItem]) -> tuple[int, int]:
    accepted_items = [item for item in items if _matches_target(item)]
    new_found = 0
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        for item in accepted_items:
            existing = await session.scalar(select(SearchResult).where(SearchResult.url == item.url))
            if existing:
                existing.last_seen = now
                existing.title = item.title or existing.title
                existing.snippet = item.snippet or existing.snippet
                continue
            session.add(
                SearchResult(
                    url=item.url,
                    search_engine=provider_name,
                    query=query,
                    title=item.title,
                    snippet=item.snippet,
                    status=f"new:{_priority(item.title, item.snippet)}",
                )
            )
            await session.flush()
            new_found += 1
        session.add(
            SearchHistory(
                search_engine=provider_name,
                query=query,
                total_found=len(accepted_items),
                new_found=new_found,
                status="ok",
                message=f"Отфильтровано: {len(items) - len(accepted_items)}; принято: {len(accepted_items)}",
            )
        )
        await session.commit()
    return new_found, len(accepted_items)


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
    except Exception as exc:
        return provider_name, query, None, exc


async def run_search() -> SearchSummary:
    summary = SearchSummary()
    if not settings.search_enabled:
        return summary

    queries = _rotated_queries()
    enabled_providers = [name for name in settings.search_providers if name in PROVIDERS]
    jobs: list[tuple[str, str]] = []

    for provider_name in enabled_providers:
        provider_queries = ["direct filters"] if provider_name in QUERYLESS_PROVIDERS else queries
        summary.providers[provider_name] = {
            "queries": len(provider_queries),
            "found": 0,
            "new": 0,
            "errors": 0,
            "blocked": 0,
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
            new_found, accepted_count = await _save_items(provider_name, query, safe_items)
        except Exception as exc:
            summary.errors += 1
            stats["errors"] += 1
            await _save_provider_status(provider_name, query, "error", str(exc))
            continue

        summary.found += accepted_count
        summary.new += new_found
        stats["found"] += accepted_count
        stats["new"] += new_found

    return summary


async def periodic_search(stop_event: asyncio.Event) -> None:
    interval_seconds = max(settings.search_interval_minutes, 15) * 60
    while not stop_event.is_set():
        try:
            await run_search()
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


async def get_search_status() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with SessionLocal() as session:
        total = await session.scalar(select(func.count(SearchResult.id))) or 0
        new_total = await session.scalar(
            select(func.count(SearchResult.id)).where(SearchResult.status.like("new:%"))
        ) or 0
        found_hour = await session.scalar(
            select(func.count(SearchResult.id)).where(SearchResult.first_seen >= hour_ago)
        ) or 0
        found_today = await session.scalar(
            select(func.count(SearchResult.id)).where(SearchResult.first_seen >= day_start)
        ) or 0
        urgent = await session.scalar(
            select(func.count(SearchResult.id)).where(SearchResult.status == "new:urgent")
        ) or 0
        last_run = await session.scalar(select(func.max(SearchHistory.searched_at)))
        engine_rows = (
            await session.execute(
                select(SearchResult.search_engine, func.count(SearchResult.id))
                .group_by(SearchResult.search_engine)
                .order_by(SearchResult.search_engine)
            )
        ).all()
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
        "enabled": settings.search_enabled,
        "providers": settings.search_providers,
        "provider_status": provider_status,
        "brave_configured": bool(settings.brave_search_api_key),
        "krisha_direct_configured": "krisha_direct" in settings.search_providers,
        "interval_minutes": max(settings.search_interval_minutes, 15),
        "queries_per_run": settings.search_queries_per_run,
        "total_results": total,
        "new_results": new_total,
        "urgent_results": urgent,
        "found_last_hour": found_hour,
        "found_today": found_today,
        "last_run": last_run,
        "by_provider": {name: count for name, count in engine_rows},
    }
