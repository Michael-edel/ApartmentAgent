from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import httpx

from app.config import SearchSettingsProxy
from app.cities import city_slug, SEARCH_CITIES
from app.seller import IDENTITY_CONFIRMED_REALTOR

settings = SearchSettingsProxy()

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
_ANCILLARY_JSON_RE = re.compile(r"^(?:recommend|related|similar|suggest|nearby|other(?:offers|listings|adverts))", re.I)
_RECAPTCHA_POLICY_RE = re.compile(
    r'<p\b[^>]*\bclass=["\'][^"\']*\bg-recaptcha-policy\b[^"\']*["\'][^>]*>.*?</p\s*>',
    re.IGNORECASE | re.DOTALL,
)


def _security_text(html: str) -> str:
    # Krisha includes this legal notice on ordinary search pages.  It is not a
    # challenge; preserve all other CAPTCHA/security text for blocking checks.
    return _RECAPTCHA_POLICY_RE.sub("", html).lower()


class KrishaSearchError(RuntimeError):
    pass


class KrishaSearchBlocked(KrishaSearchError):
    """Krisha returned a security page or rate-limit response."""


@dataclass(slots=True)
class KrishaSearchItem:
    title: str
    url: str
    snippet: str | None = None
    seller_type: str | None = None
    market_type: str | None = None
    city_evidence: set[str] = field(default_factory=set)


def _locality_key(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("cityName")
    if not isinstance(value, str):
        return None
    value = value.strip().split(",", 1)[0].strip()
    value = re.sub(r"^(?:город\s+|г\.\s*)", "", value, flags=re.I)
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі -]{2,80}", value):
        return None
    return city_slug(value) or value.casefold()


class _CardMetadataParser(HTMLParser):
    """Associate explicit metadata with its own search card only."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.roles: dict[str, set[str]] = {}
        self.markets: dict[str, set[str]] = {}
        self.cities: dict[str, set[str]] = {}
        self.catalog_urls: set[str] = set()
        self.sections: list[bool] = []
        self.city_depth: int | None = None
        self.city_text: list[str] = []
        self.city_is_explicit = False
        self.card_cities: set[str] = set()
        self.depth = 0
        self.label_depth: int | None = None
        self.label_text: list[str] = []
        self.label_kind: str | None = None
        self.specialist_footer_depth: int | None = None
        self.complex_footer_depth: int | None = None
        self.card_id: str | None = None
        self.title_url: str | None = None
        self.card_roles: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "section":
            self.sections.append("a-search-list" in (dict(attrs).get("class") or "").split())
        if tag == "a" and self.depth:
            attributes = dict(attrs)
            if "a-card__title" in (attributes.get("class") or "").split():
                self.title_url = normalize_krisha_listing_url(attributes.get("href") or "")
            return
        if tag != "div":
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if not self.depth and "a-card" in classes:
            card_id = attributes.get("data-id") or ""
            if not re.fullmatch(r"[1-9]\d{0,14}", card_id):
                return
            self.card_id, self.depth, self.card_roles, self.title_url = card_id, 1, set(), None
            self.card_cities = {city for key in ("data-city", "data-city-name")
                                if (city := _locality_key(attributes.get(key)))}
            return
        if self.depth:
            self.depth += 1
            if "a-card__city" in classes or "a-card__subtitle" in classes:
                self.city_depth, self.city_text = self.depth, []
                self.city_is_explicit = "a-card__city" in classes
            if "a-card__footer" in classes and "user-specialist" in classes:
                self.specialist_footer_depth = self.depth
            if "a-card__footer" in classes and "user-complex" in classes:
                self.complex_footer_depth = self.depth
            if "label-user-owner" in classes:
                self.label_depth, self.label_text, self.label_kind = self.depth, [], 'owner'
            elif "label-user-identified-specialist" in classes and self.specialist_footer_depth is not None:
                self.label_depth, self.label_text, self.label_kind = self.depth, [], 'specialist'
            elif "a-card__complex-label" in classes and self.complex_footer_depth is not None:
                self.label_depth, self.label_text, self.label_kind = self.depth, [], 'new'
            elif "label-user-complex" in classes:
                self.label_depth, self.label_text, self.label_kind = self.depth, [], 'new'

    def handle_data(self, data: str) -> None:
        if self.label_depth is not None:
            self.label_text.append(data)
        if self.city_depth is not None:
            self.city_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "section" and self.sections:
            self.sections.pop()
        if tag != "div" or not self.depth:
            return
        if self.depth == self.city_depth:
            text = " ".join(" ".join(self.city_text).split())
            city = _locality_key(text)
            # A subtitle may contain a street. Use a known city, or an explicit city label.
            if city and (self.city_is_explicit or re.match(r"^(?:город\s+|г\.)", text, re.I)
                         or (city_slug(city) and re.search(r",\s*(?:р-н|район)\s+", text, re.I))):
                self.card_cities.add(city)
            self.city_depth = None
        if self.depth == self.label_depth:
            label = " ".join(" ".join(self.label_text).split()).casefold()
            if self.label_kind == 'owner' and label == 'хозяин недвижимости':
                self.card_roles.add('Хозяин недвижимости')
            elif self.label_kind == 'specialist' and label == 'специалист':
                self.card_roles.add(IDENTITY_CONFIRMED_REALTOR)
            elif self.label_kind == 'new' and label == 'новостройка':
                self.card_roles.add('__new__')
            self.label_depth = None
            self.label_kind = None
        if self.depth == self.specialist_footer_depth:
            self.specialist_footer_depth = None
        if self.depth == self.complex_footer_depth:
            self.complex_footer_depth = None
        self.depth -= 1
        if not self.depth:
            if self.card_id and self.title_url == f"https://krisha.kz/a/show/{self.card_id}":
                if any(self.sections):
                    self.catalog_urls.add(self.title_url)
                if self.card_cities:
                    self.cities.setdefault(self.title_url, set()).update(self.card_cities)
                roles = self.card_roles - {'__new__'}
                self.roles.setdefault(self.title_url, set()).update(roles)
                if '__new__' in self.card_roles:
                    self.markets.setdefault(self.title_url, set()).add('new')
                if roles == {'Хозяин недвижимости'}:
                    self.markets.setdefault(self.title_url, set()).add('secondary')
            self.card_id = None


class _CardParser(HTMLParser):
    """Read a title and price from the same public search result card."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[KrishaSearchItem] = []
        self.depth = 0
        self.price_depth: int | None = None
        self.in_title = False
        self.url = ""
        self.title: list[str] = []
        self.price: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "div" and not self.depth and {"a-card__main-info", "a-card__title-info"} & set(classes):
            self.depth = 1
            self.url, self.title, self.price = "", [], []
            return
        if not self.depth:
            return
        if tag == "div":
            self.depth += 1
            if "a-card__price" in classes:
                self.price_depth = self.depth
        if tag == "a" and "a-card__title" in classes:
            self.url = attributes.get("href") or ""
            self.in_title = True

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
        if self.price_depth is not None:
            self.price.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.depth:
            return
        if tag == "a":
            self.in_title = False
        if tag == "div":
            if self.price_depth == self.depth:
                self.price_depth = None
            self.depth -= 1
            if not self.depth:
                url = normalize_krisha_listing_url(self.url)
                title = " ".join(" ".join(self.title).split())
                price = " ".join(" ".join(self.price).split())
                if url and title:
                    self.items.append(KrishaSearchItem(title=title[:300], url=url, snippet=price or None))


def build_krisha_search_url(page: int = 1, rooms: int = 2, *, owner_only: bool = False) -> str:
    city = city_slug(settings.search_city)
    if city not in SEARCH_CITIES:
        raise ValueError("Unsupported search city")
    base_url = f"https://krisha.kz/prodazha/kvartiry/{city}/"
    if rooms not in {2, 3, 4}:
        raise ValueError("rooms must be 2, 3 or 4")
    params: list[tuple[str, str]] = [
        *(("das[live.rooms][]", str(value)) for value in range(rooms, 5)),
        ("das[live.rooms][]", "5.100"),
        ("das[price][to]", str(settings.max_price_kzt)),
        ("das[live.square][from]", str(settings.min_area_m2)),
    ]
    if settings.min_price_kzt is not None:
        params.append(("das[price][from]", str(settings.min_price_kzt)))
    if settings.max_area_m2 is not None:
        params.append(("das[live.square][to]", str(settings.max_area_m2)))
    params.extend([
        ("das[_sys.hasphoto]", "1"),
        ("sort_by", "add_date-desc"),
    ])
    if owner_only:
        params.append(("das[who]", "1"))
    if settings.market_type == 'new':
        params.append(("das[novostroiki]", "1"))
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


def normalize_krisha_import_url(value: str) -> str | None:
    """Accept a public advert number or its strict Krisha URL, never another host/path."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if re.fullmatch(r"[1-9]\d{0,14}", value):
        return f"https://krisha.kz/a/show/{value}"
    try:
        parsed = urlparse(value)
        if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"krisha.kz", "www.krisha.kz"}
                or parsed.username or parsed.password or parsed.port):
            return None
    except ValueError:
        return None
    if match := re.fullmatch(r"/a/show/([1-9]\d{0,14})/?", parsed.path):
        return f"https://krisha.kz/a/show/{match.group(1)}"
    return None


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", value))).strip()


def _walk(value: object):
    if isinstance(value, dict):
        yield value
        for key, child in value.items():
            if not _ANCILLARY_JSON_RE.match(key):
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
            locations = [node.get("address"), node.get("location")]
            values = [node.get(key) for key in ("city", "cityName", "addressLocality")]
            for location in locations:
                if isinstance(location, dict):
                    values.extend(location.get(key) for key in ("city", "cityName", "addressLocality"))
            items.append(
                KrishaSearchItem(
                    title=str(title).strip() if title else "Новое объявление Krisha",
                    url=url,
                    snippet=str(description).strip()[:500] if description else None,
                    city_evidence={city for value in values if (city := _locality_key(value))},
                )
            )
    return items


def parse_krisha_search_page(html: str, *, expected_city: str | None = None) -> list[KrishaSearchItem]:
    lowered = _security_text(html)
    security_markers = (
        "captcha",
        "проверка безопасности",
        "security check",
        "cloudflare",
        "cf-ray",
        "ddos-guard",
        "access denied",
        "blocked",
        "waf",
        "js-challenge",
    )
    if any(marker in lowered for marker in security_markers):
        return []
    by_url: dict[str, KrishaSearchItem] = {}

    parser = _CardParser()
    parser.feed(html)
    for item in parser.items:
        by_url[item.url] = item

    for match in _TITLE_LINK_RE.finditer(html):
        url = normalize_krisha_listing_url(match.group("url"))
        if not url:
            continue
        title = _plain_text(match.group("title")) or "Новое объявление Krisha"
        if url not in by_url:
            by_url[url] = KrishaSearchItem(title=title[:300], url=url)

    for match in _LISTING_LINK_RE.finditer(html):
        url = normalize_krisha_listing_url(match.group("url"))
        if url and url not in by_url:
            by_url[url] = KrishaSearchItem(title="Новое объявление Krisha", url=url)

    for item in _extract_json_items(html):
        existing = by_url.get(item.url)
        if existing:
            existing.city_evidence.update(item.city_evidence)
            if existing.title == "Новое объявление Krisha" and item.title:
                existing.title = item.title[:300]
            if item.snippet and not existing.snippet:
                existing.snippet = item.snippet
        else:
            by_url[item.url] = item

    metadata = _CardMetadataParser()
    metadata.feed(html)
    for url, cities in metadata.cities.items():
        if url in by_url:
            by_url[url].city_evidence.update(cities)
    for url, roles in metadata.roles.items():
        if url in by_url and len(roles) == 1:
            by_url[url].seller_type = next(iter(roles))
    for url, markets in metadata.markets.items():
        if url in by_url and len(markets) == 1:
            by_url[url].market_type = next(iter(markets))

    selected = city_slug(expected_city)
    return [item for item in by_url.values()
            if (not metadata.catalog_urls or item.url in metadata.catalog_urls)
            and (not selected or not item.city_evidence or item.city_evidence == {selected})]


async def search_krisha_direct(rooms: int = 2, *, owner_only: bool = False) -> list[KrishaSearchItem]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-KZ,ru;q=0.9,kk-KZ;q=0.8",
        "Referer": "https://krisha.kz/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }
    result: dict[str, KrishaSearchItem] = {}
    page_limit = min(max(settings.krisha_search_pages, 1), 10)

    async with httpx.AsyncClient(
        timeout=settings.krisha_search_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for page in range(1, page_limit + 1):
            url = build_krisha_search_url(page, rooms, owner_only=owner_only)
            response: httpx.Response | None = None

            for attempt in range(3):
                try:
                    response = await client.get(url)
                    if response.status_code in {403, 429, 468}:
                        raise KrishaSearchBlocked(
                            f"Krisha ограничила поиск (HTTP {response.status_code})"
                        )
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt < 2:
                        await asyncio.sleep((2**attempt) + 1)

            if response is None or response.is_error:
                return list(result.values())

            if any(
                marker in _security_text(response.text)
                for marker in ("captcha", "проверка безопасности", "cloudflare", "access denied")
            ):
                raise KrishaSearchBlocked("Krisha запросила проверку безопасности")
            page_items = parse_krisha_search_page(response.text, expected_city=settings.search_city)
            if not page_items:
                break

            before = len(result)
            for item in page_items:
                result[item.url] = item
            if len(result) == before:
                break

            if page < page_limit:
                await asyncio.sleep(max(settings.krisha_search_delay_seconds, 0.5))

    return list(result.values())
