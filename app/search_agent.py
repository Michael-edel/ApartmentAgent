import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree

import httpx
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.models import SearchHistory, SearchResult

settings = get_settings()

DEFAULT_QUERIES = [
    'site:krisha.kz/a/show/ Астана 2-комнатная квартира 55 м²',
    'site:krisha.kz/a/show/ Астана 2-комнатная квартира 60 м²',
    'site:krisha.kz/a/show/ Астана 2-комнатная квартира до 30000000',
    'site:krisha.kz/a/show/ Астана полноценная 2-комнатная квартира',
]


@dataclass(slots=True)
class SearchSummary:
    queries: int = 0
    found: int = 0
    new: int = 0
    errors: int = 0


def _is_allowed_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ''
    return host.endswith('krisha.kz') and '/a/show/' in parsed.path


def _parse_rss(xml_text: str) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml_text)
    items: list[dict[str, str]] = []
    for node in root.findall('.//item'):
        title = (node.findtext('title') or '').strip()
        link = (node.findtext('link') or '').strip()
        description = (node.findtext('description') or '').strip()
        if link and _is_allowed_listing_url(link):
            items.append({'title': title, 'url': link, 'snippet': description})
    return items


async def _search_bing_rss(query: str) -> list[dict[str, str]]:
    url = f'https://www.bing.com/search?format=rss&q={quote_plus(query)}'
    headers = {
        'User-Agent': 'ApartmentAgent/1.0 (+personal property search; contact repository owner)',
        'Accept': 'application/rss+xml, application/xml;q=0.9, text/xml;q=0.8',
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
    return _parse_rss(response.text)


async def run_search() -> SearchSummary:
    summary = SearchSummary()
    if not settings.search_enabled:
        return summary

    for query in DEFAULT_QUERIES:
        summary.queries += 1
        try:
            items = await _search_bing_rss(query)
        except Exception as exc:
            summary.errors += 1
            async with SessionLocal() as session:
                session.add(
                    SearchHistory(
                        search_engine='bing_rss',
                        query=query,
                        total_found=0,
                        new_found=0,
                        status='error',
                        message=str(exc)[:500],
                    )
                )
                await session.commit()
            continue

        summary.found += len(items)
        new_found = 0
        async with SessionLocal() as session:
            for item in items:
                existing = await session.scalar(
                    select(SearchResult).where(SearchResult.url == item['url'])
                )
                if existing:
                    existing.last_seen = datetime.now(timezone.utc)
                    existing.title = item['title'] or existing.title
                    existing.snippet = item['snippet'] or existing.snippet
                    continue

                session.add(
                    SearchResult(
                        url=item['url'],
                        search_engine='bing_rss',
                        query=query,
                        title=item['title'] or 'Новое объявление Krisha',
                        snippet=item['snippet'] or None,
                        status='new',
                    )
                )
                new_found += 1

            session.add(
                SearchHistory(
                    search_engine='bing_rss',
                    query=query,
                    total_found=len(items),
                    new_found=new_found,
                    status='ok',
                )
            )
            await session.commit()

        summary.new += new_found

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
    async with SessionLocal() as session:
        total = await session.scalar(select(func.count(SearchResult.id))) or 0
        new_total = await session.scalar(
            select(func.count(SearchResult.id)).where(SearchResult.status == 'new')
        ) or 0
        last_run = await session.scalar(select(func.max(SearchHistory.searched_at)))
    return {
        'enabled': settings.search_enabled,
        'engine': 'bing_rss',
        'interval_minutes': max(settings.search_interval_minutes, 15),
        'total_results': total,
        'new_results': new_total,
        'last_run': last_run,
    }
