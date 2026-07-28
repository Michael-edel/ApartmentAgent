import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import SearchHistory, SearchResult
from app.search_providers import PROVIDERS, SearchItem

settings = get_settings()

DISTRICTS = [
    "Есильский район",
    "Нура район",
    "Алматы район",
    "Сарыарка район",
    "Байконур район",
]

BASE_QUERIES = [
    'site:krisha.kz/a/show/ Астана "2-комнатная квартира"',
    'site:krisha.kz/a/show/ Астана "2-комнатная" "55 м²"',
    'site:krisha.kz/a/show/ Астана "2-комнатная" "60 м²"',
    'site:krisha.kz/a/show/ Астана "2-комнатная" "65 м²"',
    'site:krisha.kz/a/show/ Астана "2-комнатная" "70 м²"',
    'site:krisha.kz/a/show/ Астана "2-комнатная" "30 000 000"',
    'site:krisha.kz/a/show/ Астана "двухкомнатная квартира"',
    'site:krisha.kz/a/show/ Астана "полноценная 2-комнатная"',
]

SEARCH_QUERIES = BASE_QUERIES + [
    f'site:krisha.kz/a/show/ Астана "2-комнатная" "{district}"'
    for district in DISTRICTS
]


@dataclass(slots=True)
class SearchSummary:
    queries: int = 0
    found: int = 0
    new: int = 0
    errors: int = 0
    providers: dict[str, dict[str, int]] = field(default_factory=dict)


def _priority(title: str, snippet: str | None) -> str:
    text = f"{title} {snippet or ''}".lower().replace("\xa0", " ")
    strong_signals = ("55 м", "56 м", "57 м", "58 м", "59 м", "60 м", "61 м", "62 м", "63 м", "64 м", "65 м", "66 м", "67 м", "68 м", "69 м", "70 м")
    if "2-комнат" in text and any(signal in text for signal in strong_signals):
        return "urgent"
    if "2-комнат" in text or "двухкомнат" in text:
        return "good"
    return "normal"


def _rotated_queries() -> list[str]:
    count = min(max(settings.search_queries_per_run, 1), len(SEARCH_QUERIES))
    slot = int(datetime.now(timezone.utc).timestamp() // (max(settings.search_interval_minutes, 15) * 60))
    start = (slot * count) % len(SEARCH_QUERIES)
    return [SEARCH_QUERIES[(start + offset) % len(SEARCH_QUERIES)] for offset in range(count)]


async def _save_items(provider_name: str, query: str, items: list[SearchItem]) -> int:
    new_found = 0
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        for item in items:
            existing = await session.scalar(select(SearchResult).where(SearchResult.url == item.url))
            if existing:
                existing.last_seen = now
                existing.title = item.title or existing.title
                existing.snippet = item.snippet or existing.snippet
                continue

            priority = _priority(item.title, item.snippet)
            session.add(
                SearchResult(
                    url=item.url,
                    search_engine=provider_name,
                    query=query,
                    title=item.title,
                    snippet=item.snippet,
                    status=f"new:{priority}",
                )
            )
            new_found += 1

        session.add(
            SearchHistory(
                search_engine=provider_name,
                query=query,
                total_found=len(items),
                new_found=new_found,
                status="ok",
            )
        )
        await session.commit()
    return new_found


async def _save_error(provider_name: str, query: str, exc: Exception) -> None:
    async with SessionLocal() as session:
        session.add(
            SearchHistory(
                search_engine=provider_name,
                query=query,
                total_found=0,
                new_found=0,
                status="error",
                message=str(exc)[:500],
            )
        )
        await session.commit()


async def run_search() -> SearchSummary:
    summary = SearchSummary()
    if not settings.search_enabled:
        return summary

    queries = _rotated_queries()
    enabled_providers = [name for name in settings.search_providers if name in PROVIDERS]

    for provider_name in enabled_providers:
        provider = PROVIDERS[provider_name]
        provider_stats = summary.providers.setdefault(provider_name, {"queries": 0, "found": 0, "new": 0, "errors": 0})
        for query in queries:
            summary.queries += 1
            provider_stats["queries"] += 1
            try:
                items = await provider(query)
                new_found = await _save_items(provider_name, query, items)
            except Exception as exc:
                summary.errors += 1
                provider_stats["errors"] += 1
                await _save_error(provider_name, query, exc)
                continue

            summary.found += len(items)
            summary.new += new_found
            provider_stats["found"] += len(items)
            provider_stats["new"] += new_found
            await asyncio.sleep(0.8)

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

    return {
        "enabled": settings.search_enabled,
        "providers": settings.search_providers,
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
