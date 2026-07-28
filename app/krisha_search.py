from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from app.config import get_settings

settings = get_settings()

_LISTING_LINK_RE = re.compile(
    r'href=["\'](?P<url>(?:https?://(?:www\.)?krisha\.kz)?/a/show/[^"\'#?\s]+)',
    re.IGNORECASE,
)
_TITLE_LINK_RE = re.compile(
    r'<a[^>]+class=["\'][^"\']*a-card__title[^"\']*["\'][^>]+href=["\'](?P<url>[^"\']+)["\'][^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_JSON_SCRIPT_RE = re.compile(
    r'<script[^>]+(?:id=["\']jsdata["\']|type=["\']application/(?:ld\+)?json["\'])[^>]*>(?P<data>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


class KrishaSearchError(RuntimeError):
    pass


@dataclass(slots=True)
class KrishaSearchItem:
    title: str
    url: str
    snippet: str | None = None


def build_krisha_search_url(page: int = 1) -> str:
    base_url = "https://krisha.kz/prodazha/kvartiry/astana/"
    params: list[tuple[str, str]] = [
        ("das[live.rooms]", "2"),
        ("das[price][to]", str(settings.max_price_kzt)),
        ("das[live.square][from]", str(settings.min_area_m2)),
        ("das[live.square][to]", str(settings.max_area_m2)),
        ("das[_sys.hasphoto]", "1"),
        ("sort_by", "add_date-desc"),
    ]
    if page > 1:
        params.append(("page", str(page)))
    return f"{base_url}?{urlencode(params)}"


def normalize_krisha_listing_url(raw_url: str) -> str | None:
    absolute = urljoin("https://krisha.kz", unescape(raw_url).strip())
    parsed = urlparse(absolute)
    host = (parsed.hostname or "").lower()
    if host not in {"krisha.kz", "www.krisha.kz"} or "/a/show/" not in parsed.path:
        return None
    return urlunparse(("https", "krisha.kz", parsed.path.rstrip("/"), "", "", ""))


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", value))).strip()


def _walk(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _extract_json_items(html: str) -> list[KrishaSearchItem]:
    items: list[KrishaSearchItem] = []
    for match in _JSON_SCRIPT_RE.finditer(html):
        raw = unescape(match.group("data")).strip()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk(payload):
            raw_url = node.get("url") or node.get("link") or node.get("advertUrl")
            if not isinstance(raw_url, str):
                continue
            url = normalize_krisha_listing_url(raw_url)
            if not url:
                continue
            title = node.get("title") or node.get("name") or node.get("heading")
            description = node.get("description") or node.get("text")
            items.append(
                KrishaSearchItem(
                    title=str(title).strip() if title else "Новое объявление Krisha",
                    url=url,
                    snippet=str(description).strip()[:500] if description else None,
                )
            )
    return items


def parse_krisha_search_page(html: str) -> list[KrishaSearchItem]:
    lowered = html.lower()
    if "captcha" in lowered or "проверка безопасности" in lowered:
        raise KrishaSearchError("Krisha запросила проверку безопасности")

    by_url: dict[str, KrishaSearchItem] = {}

    for match in _TITLE_LINK_RE.finditer(html):
        url = normalize_krisha_listing_url(match.group("url"))
        if not url:
            continue
        title = _plain_text(match.group("title")) or "Новое объявление Krisha"
        by_url[url] = KrishaSearchItem(title=title[:300], url=url)

    for match in _LISTING_LINK_RE.finditer(html):
        url = normalize_krisha_listing_url(match.group("url"))
        if url and url not in by_url:
            by_url[url] = KrishaSearchItem(title="Новое объявление Krisha", url=url)

    for item in _extract_json_items(html):
        existing = by_url.get(item.url)
        if existing:
            if existing.title == "Новое объявление Krisha" and item.title:
                existing.title = item.title[:300]
            if item.snippet:
                existing.snippet = item.snippet
        else:
            by_url[item.url] = item

    return list(by_url.values())


async def search_krisha_direct(_: str = "") -> list[KrishaSearchItem]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-KZ,ru;q=0.9,en;q=0.5",
        "Cache-Control": "no-cache",
    }
    result: dict[str, KrishaSearchItem] = {}
    page_limit = min(max(settings.krisha_search_pages, 1), 10)

    async with httpx.AsyncClient(
        timeout=settings.krisha_search_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for page in range(1, page_limit + 1):
            url = build_krisha_search_url(page)
            last_error: Exception | None = None
            response: httpx.Response | None = None
            for attempt in range(3):
                try:
                    response = await client.get(url)
                    if response.status_code in {403, 429}:
                        raise KrishaSearchError(
                            f"Krisha ограничила прямой доступ: HTTP {response.status_code}"
                        )
                    response.raise_for_status()
                    break
                except (httpx.HTTPError, KrishaSearchError) as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
            if response is None or response.is_error:
                raise KrishaSearchError(str(last_error or "Не удалось загрузить выдачу Krisha"))

            page_items = parse_krisha_search_page(response.text)
            if not page_items:
                if page == 1:
                    raise KrishaSearchError(
                        "Krisha не вернула карточки объявлений: возможно, изменилась разметка или сработала защита"
                    )
                break

            before = len(result)
            for item in page_items:
                result[item.url] = item
            if len(result) == before:
                break

            if page < page_limit:
                await asyncio.sleep(max(settings.krisha_search_delay_seconds, 0.5))

    return list(result.values())
