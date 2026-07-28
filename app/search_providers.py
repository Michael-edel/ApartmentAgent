from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from xml.etree import ElementTree

import httpx


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
    scheme = "https"
    host = (parsed.hostname or "krisha.kz").lower()
    path = parsed.path.rstrip("/")
    return f"{scheme}://{host}{path}"


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


async def search_bing_rss(query: str) -> list[SearchItem]:
    url = f"https://www.bing.com/search?format=rss&q={quote_plus(query)}"
    headers = {
        "User-Agent": "ApartmentAgent/0.7 (+personal property search)",
        "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
    return _parse_rss(response.text)


PROVIDERS = {
    "bing_rss": search_bing_rss,
}
