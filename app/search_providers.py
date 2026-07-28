from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from xml.etree import ElementTree

import httpx

from app.config import get_settings
from app.krisha_search import KrishaSearchError
from app.krisha_search import search_krisha_direct as fetch_krisha_direct

settings = get_settings()


@dataclass(slots=True)
class SearchItem:
    title: str
    url: str
    snippet: str | None = None


def is_allowed_listing_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host.endswith("krisha.kz") and "/a/show/" in parsed.path


def normalize_listing_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "krisha.kz").lower()
    path = parsed.path.rstrip("/")
    return f"https://{host}{path}"


def _decode_bing_link(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.hostname or "bing.com" not in parsed.hostname:
        return url
    values = parse_qs(parsed.query)
    target = values.get("url") or values.get("u")
    return unquote(target[0]) if target else url


def _parse_rss(xml_text: str) -> list[SearchItem]:
    root = ElementTree.fromstring(xml_text)
    items: list[SearchItem] = []
    for node in root.findall(".//item"):
        title = unescape((node.findtext("title") or "").strip())
        raw_link = (node.findtext("link") or "").strip()
        link = _decode_bing_link(raw_link)
        description = unescape((node.findtext("description") or "").strip())
        if link and is_allowed_listing_url(link):
            items.append(
                SearchItem(
                    title=title or "Новое объявление Krisha",
                    url=normalize_listing_url(link),
                    snippet=description or None,
                )
            )
    return items


def _clean_web_query(query: str) -> str:
    cleaned = re.sub(r"site:\S+", " ", query, flags=re.IGNORECASE)
    cleaned = cleaned.replace('"', " ")
    cleaned = re.sub(r"\b(?:55|60|65|70)\s*м(?:²|2)?\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b30\s*000\s*000\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "krisha.kz астана 2-комнатная квартира"


async def search_bing_rss(query: str) -> list[SearchItem]:
    clean_query = _clean_web_query(query)
    url = f"https://www.bing.com/search?format=rss&q={quote_plus(clean_query)}&count=50"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml;q=0.9, text/html;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
    return _parse_rss(response.text)


async def search_brave(query: str) -> list[SearchItem]:
    if not settings.brave_search_api_key:
        return []

    clean_query = _clean_web_query(query)
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": settings.brave_search_api_key,
        "User-Agent": "ApartmentAgent/0.10",
    }
    params = {
        "q": clean_query,
        "count": 50,
        "offset": 0,
        "country": "KZ",
        "search_lang": "ru",
        "ui_lang": "ru-RU",
        "safesearch": "moderate",
        "freshness": settings.search_freshness or "week",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        response = await client.get("https://api.search.brave.com/res/v1/web/search", params=params)
        response.raise_for_status()
        payload = response.json()

    results = payload.get("web", {}).get("results", [])
    items: list[SearchItem] = []
    for result in results:
        url = str(result.get("url") or "")
        if not is_allowed_listing_url(url):
            continue
        items.append(
            SearchItem(
                title=unescape(str(result.get("title") or "Новое объявление Krisha")),
                url=normalize_listing_url(url),
                snippet=unescape(str(result.get("description") or "")) or None,
            )
        )
    return items


async def search_krisha_direct(_: str) -> list[SearchItem]:
    try:
        items = await fetch_krisha_direct()
    except KrishaSearchError:
        return []
    return [SearchItem(title=item.title, url=item.url, snippet=item.snippet) for item in items]


PROVIDERS = {
    "krisha_direct": search_krisha_direct,
    "brave": search_brave,
    "bing_rss": search_bing_rss,
}
