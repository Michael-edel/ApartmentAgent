import json
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urlparse, urlunparse

import httpx
from pydantic import HttpUrl

from app.schemas import ListingCreate, plain_detail_text
from app.mortgage import mortgage_from_html, mortgage_value, parse_mortgage_text


class _FieldNode:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent=None):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[_FieldNode | str] = []

    def hidden(self) -> bool:
        return (self.tag in {"script", "style", "noscript"}
                or "hidden" in self.attrs
                or str(self.attrs.get("aria-hidden", "")).lower() == "true"
                or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)",
                                  self.attrs.get("style") or "", re.I)))

    def text(self) -> str:
        chunks, pending = [], list(reversed(self.children))
        while pending:
            child = pending.pop()
            if isinstance(child, str):
                chunks.append(child)
            elif not child.hidden():
                pending.extend(reversed(child.children))
        return re.sub(r"\s+", " ", " ".join(chunks)).strip()

    def prose_text(self) -> str:
        parts, pending = [], [self]
        while pending:
            child = pending.pop()
            if isinstance(child, str):
                parts.append(child)
            elif not child.hidden():
                if child.tag == "br":
                    parts.append("\n")
                    continue
                block = child.tag in {"p", "div", "li"}
                if block:
                    parts.append("\n")
                    pending.append("\n")
                pending.extend(reversed(child.children))
        raw = "".join(parts).replace("\r\n", "\n").replace("\r", "\n")
        lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in raw.split("\n")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class _BathroomFieldHTMLParser(HTMLParser):
    _void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                  "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _FieldNode("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _FieldNode(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self._void_tags:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._void_tags:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def visible_nodes(self):
        pending = [self.root]
        while pending:
            node = pending.pop()
            if node.hidden():
                continue
            yield node
            pending.extend(reversed([child for child in node.children if isinstance(child, _FieldNode)]))

    def properties(self) -> dict[str, str]:
        result = {}
        common = ("Состояние квартиры", "Год постройки", "Жилой комплекс", "Общая площадь", "Тип дома",
                  "Высота потолков", "В ипотеку", "В залоге", "Площадь кухни", "Санузел", "Санузлы",
                  "Город", "Район", "Адрес", "Ремонт", "Ипотека", "Этаж", "Площадь", "Балкон", "Парковка",
                  "Интернет", "Безопасность", "Мебель", "Дверь", "Пол", "Потолки", "Телефон", "Обмен")
        for node in self.visible_nodes():
            label = value = None
            children = [child for child in node.children if isinstance(child, _FieldNode) and not child.hidden()]
            if node.tag == "dt" and node.parent:
                siblings = [child for child in node.parent.children if isinstance(child, _FieldNode)]
                index = siblings.index(node)
                if index + 1 < len(siblings) and siblings[index + 1].tag == "dd" and not siblings[index + 1].hidden():
                    label, value = node.text(), siblings[index + 1].text()
            elif set((node.attrs.get("class") or "").split()).intersection({"a-list__item", "offer__info-item"}):
                if len(children) >= 2:
                    label, value = children[0].text(), " ".join(child.text() for child in children[1:])
                else:
                    text = node.text()
                    for candidate in common:
                        match = re.fullmatch(re.escape(candidate) + r"\s*[:—-]?\s+(.+)", text, re.I)
                        if match:
                            label, value = candidate, match.group(1)
                            break
                    if label is None and ":" in text:
                        label, value = text.split(":", 1)
            if label is None or value is None:
                continue
            label, value = plain_detail_text(label).rstrip(":—- "), plain_detail_text(value)
            if label and value and len(label) <= 100 and len(value) <= 1500 and len(result) < 60:
                result[label] = value
        return result


    def values(self):
        label = re.compile(r"^(?:санузел|санузлы|с\s*/\s*у)\s*[:—-]?\s*(.*)$", re.I)
        for node in self.visible_nodes():
            classes = set((node.attrs.get("class") or "").split())
            named = (node.attrs.get("data-name") in {"flat.toilet", "bathrooms"}
                     or node.attrs.get("data-testid") == "bathrooms")
            if named or classes.intersection({"a-list__item", "offer__info-item"}):
                text = node.text()
                match = label.fullmatch(text)
                if match:
                    yield match.group(1)
                elif named:
                    yield text
            elif node.tag == "dt" and label.fullmatch(node.text()):
                siblings = [child for child in node.parent.children if isinstance(child, _FieldNode)]
                index = siblings.index(node)
                if index + 1 < len(siblings) and siblings[index + 1].tag == "dd":
                    match = label.fullmatch(node.text())
                    if not match.group(1) and not siblings[index + 1].hidden():
                        yield siblings[index + 1].text()


def _public_details(html: str) -> tuple[_BathroomFieldHTMLParser, dict[str, str]]:
    parser = _BathroomFieldHTMLParser()
    parser.feed(html)
    return parser, parser.properties()


def _property_value(details: dict[str, str], *labels: str) -> str | None:
    wanted = {label.casefold() for label in labels}
    return next((value for label, value in details.items() if label.casefold() in wanted), None)


def _advert_description(parser: _BathroomFieldHTMLParser) -> str | None:
    for node in parser.visible_nodes():
        classes = set((node.attrs.get("class") or "").split())
        if ("offer__description" in classes or node.attrs.get("data-testid") in {"description", "advert-description"}
                or (node.attrs.get("itemprop") == "description" and node.tag != "meta")):
            text = node.prose_text()
            if text:
                return text[:20_000]
    return None


def _seller_fields(parser: _BathroomFieldHTMLParser, data: list[Any]) -> tuple[str | None, str | None]:
    name = _find_text(data, {"sellerName", "ownerName", "advertiserName"})
    seller_type = _find_text(data, {"sellerType", "ownerType", "advertiserType"})
    for node in parser.visible_nodes():
        if node.attrs.get("data-testid") == "seller-name":
            name = node.text()[:200] or name
        if node.text().casefold().rstrip(": ") != "автор объявления":
            continue
        card = node.parent
        if not card:
            continue
        card_text = card.text()
        if len(card_text) > 1500:
            continue
        type_match = re.search(r"Хозяин недвижимости|Агентство недвижимости|Специалист по недвижимости|Крыша Агент|Риелтор|Риэлтор", card_text, re.I)
        if type_match:
            seller_type = type_match.group(0)
        for child in card.children:
            if not isinstance(child, _FieldNode) or child.hidden():
                continue
            if (child.tag in {"h2", "h3", "h4"} or child.attrs.get("itemprop") == "name"
                    or child.attrs.get("data-testid") == "seller-name"):
                candidate = child.text()
                if candidate and candidate.casefold() != "автор объявления" and not re.search(r"\+?\d[\d ()-]{5,}", candidate):
                    if not type_match or candidate.casefold() != type_match.group(0).casefold():
                        name = candidate[:200]
    return name[:200] if name else None, seller_type[:100] if seller_type else None


def _krisha_comparison_fields(parser: _BathroomFieldHTMLParser) -> dict[str, Any]:
    result = {"krisha_district_price_per_m2": None, "krisha_city_price_per_m2": None,
              "krisha_comparison_district": None}
    blocks = [node for node in parser.visible_nodes() if node.tag in {"div", "section", "article"}
              and len(node.text()) <= 1800 and re.search(r"Цена\s*м[²2]\s*в похожих квартирах", node.text(), re.I)
              and "Похожие квартиры в Астане" in node.text()]
    for block in sorted(blocks, key=lambda node: len(node.text())):
        pending = [block]
        for node in pending:
            children = [child for child in node.children if isinstance(child, _FieldNode) and not child.hidden()]
            pending.extend(children)
            text = node.text()
            if node.tag not in {"div", "td", "li"} or len(text) > 220:
                continue
            first = re.fullmatch(r"(\d[\d ]*)\s*(?:₸|тг|тенге)(?:\s*/\s*м[²2])?\s*Похожие квартиры в (.+)", text, re.I)
            last = re.fullmatch(r"Похожие квартиры в (.+?)\s+(\d[\d ]*)\s*(?:₸|тг|тенге)(?:\s*/\s*м[²2])?", text, re.I)
            if not first and not last:
                continue
            label, digits = (first.group(2), first.group(1)) if first else (last.group(1), last.group(2))
            value = int(digits.replace(" ", ""))
            if not 0 < value <= 1_000_000_000:
                continue
            if label.casefold() == "астане":
                result["krisha_city_price_per_m2"] = value
            elif re.search(r"(?:район[еа]?|р-н(?:е)?)$", label, re.I):
                result["krisha_district_price_per_m2"] = value
                result["krisha_comparison_district"] = label[:150]
        if result["krisha_district_price_per_m2"] or result["krisha_city_price_per_m2"]:
            return result
    return result

def _bathroom_field_value(raw: Any) -> tuple[int | None, bool]:
    if isinstance(raw, bool):
        return None, False
    value = str(raw).strip()
    if re.fullmatch(r"раздельный|совмещ[её]нный", value, re.I):
        return 1, False
    match = re.fullmatch(r"(\d{1,2})(?:\s*с\s*/\s*у)?\s*(и\s+более|и\s+больше|\+)?", value, re.I)
    if match and int(match.group(1)) <= 20:
        return int(match.group(1)), bool(match.group(2))
    return None, False


def _bathrooms_fields(html: str, data: list[Any]) -> tuple[int | None, bool]:
    # Read complete nested property rows, with visible fields ahead of JSON.
    parser = _BathroomFieldHTMLParser()
    parser.feed(html)
    for value in parser.values():
        count, lower = _bathroom_field_value(value)
        if count is not None:
            return count, lower
    keys = {"bathroomscount", "bathrooms_count", "numberofbathroomstotal", "numberofbathrooms"}
    for root in data:
        for item in _walk(root):
            for key, raw in item.items():
                if str(key).lower() not in keys:
                    continue
                count, lower = _bathroom_field_value(raw)
                if count is not None:
                    return count, lower
    return None, False


def _mortgage_fields(html: str, data: list[Any], description: str = "", property_details: dict[str, str] | None = None) -> bool | None:
    details = property_details if property_details is not None else _public_details(html)[1]
    explicit = _property_value(details, "Ипотека", "В ипотеку")
    if explicit is not None:
        parsed = mortgage_value(explicit)
        if parsed is not None:
            return parsed
    values = []
    for root in data:
        for item in _walk(root):
            for key, value in item.items():
                if str(key).casefold() in {"mortgagesupported", "mortgage_supported", "ismortgageavailable"}:
                    values.append(mortgage_value(value))
    values.extend([mortgage_from_html(html), parse_mortgage_text(description)])
    if any(value is False for value in values):
        return False
    return True if any(value is True for value in values) else None

_PRICE_RE = re.compile(r"(?P<price>\d[\d\s\u00a0]{5,})\s*(?:₸|тг|тенге)", re.IGNORECASE)
_AREA_RE = re.compile(r"(?P<area>\d{2,3}(?:[.,]\d+)?)\s*м(?:²|2)", re.IGNORECASE)
_ROOMS_RE = re.compile(r"(?P<rooms>\d+)\s*[- ]?комнат", re.IGNORECASE)
_FLOOR_RE = re.compile(
    r"(?P<floor>\d+)\s*(?:этаж|эт\.)\s*(?:из|/)\s*(?P<total>\d+)", re.IGNORECASE
)
_YEAR_RE = re.compile(
    r"(?:год постройки|построен(?:а|о)? в)\D{0,20}(?P<year>19\d{2}|20\d{2})",
    re.IGNORECASE,
)


class ListingImportError(ValueError):
    pass


def _is_krisha_host(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    return normalized == "krisha.kz" or normalized.endswith(".krisha.kz")


def _clean_number(value: str) -> int:
    return int(re.sub(r"\D", "", value))


def _extract_meta(html: str, property_name: str) -> str | None:
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(property_name)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return unescape(match.group(1)).strip()
    return None


def _json_scripts(html: str) -> list[Any]:
    values: list[Any] = []
    patterns = [
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
    ]
    for pattern in patterns:
        for raw in re.findall(pattern, html, flags=re.IGNORECASE | re.DOTALL):
            try:
                values.append(json.loads(unescape(raw).strip()))
            except (json.JSONDecodeError, TypeError):
                continue
    return values


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d,.]", "", value).replace(",", ".")
        try:
            result = float(cleaned)
            return result if result > 0 else None
        except ValueError:
            return None
    return None


def _coordinate(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", ".")
        try:
            return float(re.sub(r"[^0-9+-.]", "", cleaned))
        except ValueError:
            return None
    return None


def _is_astana_coordinate_pair(latitude: float | None, longitude: float | None) -> bool:
    """Accept only coordinates inside the configured Astana search area."""
    return (
        latitude is not None
        and longitude is not None
        and 50 <= latitude <= 52
        and 69 <= longitude <= 73
    )


def _find_numeric(data: list[Any], keys: set[str], minimum: float, maximum: float) -> float | None:
    normalized = {key.lower() for key in keys}
    for root in data:
        for item in _walk(root):
            for key, value in item.items():
                if str(key).lower() not in normalized:
                    continue
                candidate = _number(value)
                if candidate is not None and minimum <= candidate <= maximum:
                    return candidate
    return None


def _find_coordinate(data: list[Any], keys: set[str], minimum: float, maximum: float) -> float | None:
    normalized = {key.lower() for key in keys}
    for root in data:
        for item in _walk(root):
            for key, value in item.items():
                if str(key).lower() not in normalized:
                    continue
                candidate = _coordinate(value)
                if candidate is not None and minimum <= candidate <= maximum:
                    return candidate
    return None


def _text_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, dict):
        return None
    for key in ("formattedAddress", "fullAddress", "displayAddress"):
        candidate = _text_value(value.get(key))
        if candidate:
            return candidate
    parts: list[str] = []
    for key in (
        "streetAddress",
        "addressLocality",
        "addressRegion",
    ):
        candidate = _text_value(value.get(key))
        if candidate and candidate not in parts:
            parts.append(candidate)
    return ", ".join(parts) or None


def _find_text(data: list[Any], keys: set[str]) -> str | None:
    normalized = {key.lower() for key in keys}
    for root in data:
        for item in _walk(root):
            for key, value in item.items():
                if str(key).lower() in normalized:
                    candidate = _text_value(value)
                    if candidate:
                        return candidate
    return None


def _valid_property_address(value: str | None) -> str | None:
    text = re.sub(r"\s+", " ", value or "").strip()
    if 5 <= len(text) <= 500 and not re.search(r"работает\s+в\s+компании", text, re.I):
        return text
    return None


def _find_address(data: list[Any]) -> str | None:
    keys = {"address", "streetaddress", "formattedaddress", "fulladdress", "addressline", "displayaddress"}
    for root in data:
        for item in _walk(root):
            for key, value in item.items():
                if str(key).lower() in keys:
                    candidate = _valid_property_address(_text_value(value))
                    if candidate:
                        return candidate
    return None


def _find_district(data: list[Any], combined: str) -> str | None:
    names = r"Есильский|Нура|Алматы|Сарыарка|Байконур"

    def labelled_district(text: str) -> str | None:
        for pattern in (
            rf"(?<!\w)(?:район|р-н)\s+(?P<name>{names})(?!\w)",
            rf"(?<!\w)(?P<name>{names})\s+(?:район|р-н)(?!\w)",
        ):
            match = re.search(pattern, text, re.I)
            if match:
                return f"{match.group('name')} район"
        return None

    district = _find_text(data, {"district", "districtName", "areaName"})
    if not district:
        region = _find_text(data, {"regionName", "addressRegion"})
        if region and re.search(r"\b(?:район|р-н)\b", region, re.IGNORECASE):
            district = region
    if district:
        return labelled_district(district) or re.sub(r"\s+р-н\b", " район", district, flags=re.IGNORECASE)
    return labelled_district(combined)


def _address_from_text(value: str) -> str | None:
    patterns = (
        re.compile(
            r"(?:№\s*\d+\s*[:：]\s*|Продажа[^:—]{0,120}[:：]\s*)"
            r"(?P<address>[^—\n]{3,120}?),\s*(?:Астана|Astana)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?P<address>(?:ул\.?|улица|просп\.?|проспект|пер\.?|переулок|"
            r"мкр\.?|микрорайон|шоссе|набережная)\s+[^,\n;]{2,80},?\s+"
            r"\d+[A-Za-zА-Яа-яЁё]?)",
            re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(value)
        if match:
            address = re.sub(r"\s+", " ", match.group("address")).strip(" ,.-")
            if 5 <= len(address) <= 180:
                return address
    return None


def _find_html_coordinate(html: str, keys: set[str], minimum: float, maximum: float) -> float | None:
    names = "|".join(re.escape(key) for key in keys)
    pattern = rf"(?:data[-_]?)?(?:{names})[\"']?\s*[:=]\s*[\"']?([-+]?\d+(?:[.,]\d+)?)"
    match = re.search(pattern, html, re.IGNORECASE)
    if not match:
        return None
    candidate = _coordinate(match.group(1))
    return candidate if candidate is not None and minimum <= candidate <= maximum else None


def _coordinates_from_html(html: str) -> tuple[float | None, float | None]:
    decoded = unquote(html)
    match = re.search(
        r"(?:[?&#](?:ll|center|pt|coords|coordinates)=)"
        r"(?P<longitude>[-+]?\d{1,3}(?:\.\d+)?)[,%20]+"
        r"(?P<latitude>[-+]?\d{1,2}(?:\.\d+)?)",
        decoded,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    longitude = _coordinate(match.group("longitude"))
    latitude = _coordinate(match.group("latitude"))
    if longitude is not None and not -180 <= longitude <= 180:
        longitude = None
    if latitude is not None and not -90 <= latitude <= 90:
        latitude = None
    if not _is_astana_coordinate_pair(latitude, longitude):
        return None, None
    return latitude, longitude


def _find_image_urls(data: list[Any]) -> list[str]:
    image_keys = {
        "image",
        "images",
        "photo",
        "photos",
        "photoUrls",
        "imageUrls",
        "gallery",
        "pictures",
    }
    urls: list[str] = []
    for root in data:
        for item in _walk(root):
            for key, value in item.items():
                if str(key).lower() not in {item.lower() for item in image_keys}:
                    continue
                values = value if isinstance(value, list) else [value]
                for candidate in values:
                    if isinstance(candidate, dict):
                        candidate = candidate.get("url") or candidate.get("src")
                    if not isinstance(candidate, str):
                        continue
                    url = unescape(candidate).strip()
                    if url.startswith("//"):
                        url = "https:" + url
                    if url.startswith(("http://", "https://")) and url not in urls:
                        urls.append(url)
    return urls[:30]


def _embedded_number(html: str, keys: set[str], minimum: float, maximum: float) -> float | None:
    for key in keys:
        pattern = rf'["\']{re.escape(key)}["\']\s*:\s*["\']?([\d\s.,]+)'
        for match in re.finditer(pattern, html, re.IGNORECASE):
            candidate = _number(match.group(1))
            if candidate is not None and minimum <= candidate <= maximum:
                return candidate
    return None


async def import_krisha_listing(source_url: str) -> ListingCreate:
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not _is_krisha_host(host):
        raise ListingImportError("Разрешены только ссылки krisha.kz")

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-KZ,ru;q=0.9,en;q=0.5",
        "Cache-Control": "no-cache",
    }
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
            response = await client.get(source_url)
    except httpx.TooManyRedirects as exc:
        raise ListingImportError("Источник создал слишком много перенаправлений") from exc
    except httpx.HTTPError as exc:
        raise ListingImportError("Не удалось загрузить объявление") from exc

    final_host = (response.url.host or "").lower()
    if not _is_krisha_host(final_host):
        raise ListingImportError(
            f"Krisha перенаправила запрос на внешний домен {final_host or 'неизвестен'}. "
            "Откройте объявление в Safari, скопируйте конечный адрес и вставьте его снова"
        )
    if response.status_code in {403, 429, 468}:
        raise ListingImportError(
            f"Источник ограничил автоматический доступ (HTTP {response.status_code}). "
            "Откройте объявление в браузере и используйте браузерный импорт или введите данные вручную"
        )
    if response.status_code != 200:
        raise ListingImportError(f"Источник вернул HTTP {response.status_code}")

    html = response.text
    lowered_html = html.lower()
    if "captcha" in lowered_html or "проверка безопасности" in lowered_html:
        raise ListingImportError("Источник запросил проверку безопасности. Введите данные вручную")

    title = _extract_meta(html, "og:title") or _extract_meta(html, "twitter:title")
    description = _extract_meta(html, "og:description") or _extract_meta(html, "description") or ""
    meta_images = [
        value
        for name in ("og:image", "twitter:image")
        if (value := _extract_meta(html, name))
    ]
    data = _json_scripts(html)
    detail_parser, property_details = _public_details(html)
    description = _advert_description(detail_parser) or description
    bathrooms_count, bathrooms_lower_bound = _bathrooms_fields(html, data)

    price = _find_numeric(data, {"price", "price_kzt", "amount", "value"}, 500_000, 2_000_000_000)
    area = _find_numeric(data, {"area", "area_m2", "square", "squaremeter", "floorSize", "totalArea"}, 10, 1000)
    rooms = _find_numeric(data, {"rooms", "roomCount", "numberOfRooms", "roomsCount"}, 1, 20)
    floor = _find_numeric(data, {"floor", "floorNumber"}, 1, 200)
    total_floors = _find_numeric(data, {"floors", "floorsTotal", "floorCount", "numberOfFloors"}, 1, 200)
    year = _find_numeric(data, {"year", "buildYear", "buildingYear", "yearBuilt"}, 1900, 2100)

    price = price or _embedded_number(html, {"price", "priceKzt", "amount"}, 500_000, 2_000_000_000)
    area = area or _embedded_number(html, {"area", "areaM2", "square", "totalArea"}, 10, 1000)
    rooms = rooms or _embedded_number(html, {"rooms", "roomCount", "numberOfRooms"}, 1, 20)
    floor = floor or _embedded_number(html, {"floor", "floorNumber"}, 1, 200)
    total_floors = total_floors or _embedded_number(html, {"floorsTotal", "floorCount"}, 1, 200)
    year = year or _embedded_number(html, {"buildYear", "buildingYear", "yearBuilt"}, 1900, 2100)

    plain_text = unescape(re.sub(r"<[^>]+>", " ", html[:800_000]))
    plain_text = re.sub(r"\s+", " ", plain_text)
    combined = " ".join(filter(None, [title, description, plain_text]))

    price_match = _PRICE_RE.search(combined)
    area_match = _AREA_RE.search(combined)
    rooms_match = _ROOMS_RE.search(combined)
    floor_match = _FLOOR_RE.search(combined)
    year_match = _YEAR_RE.search(combined)

    price = int(price or (_clean_number(price_match.group("price")) if price_match else 0)) or None
    area = float(area or (area_match.group("area").replace(",", ".") if area_match else 0)) or None
    rooms = int(rooms or (rooms_match.group("rooms") if rooms_match else 0)) or None
    floor = int(floor or (floor_match.group("floor") if floor_match else 0)) or None
    total_floors = int(total_floors or (floor_match.group("total") if floor_match else 0)) or None
    year = int(year or (year_match.group("year") if year_match else 0)) or None
    visible_floor = _property_value(property_details, "Этаж")
    if visible_floor and (match := re.fullmatch(r"(\d{1,3})\s*(?:из|/)\s*(\d{1,3})(?:\s*этаж)?", visible_floor, re.I)):
        floor, total_floors = int(match.group(1)), int(match.group(2))
    visible_year = _property_value(property_details, "Год постройки")
    if visible_year and re.fullmatch(r"(?:19|20)\d{2}", visible_year):
        year = int(visible_year)

    if not price or not area or not rooms:
        missing = []
        if not price:
            missing.append("цену")
        if not area:
            missing.append("площадь")
        if not rooms:
            missing.append("число комнат")
        raise ListingImportError("Не удалось определить " + ", ".join(missing) + ". Заполните форму вручную")

    building_type = None
    explicit_building_type = _property_value(property_details, "Тип дома", "Материал стен")
    lowered = (explicit_building_type or combined).lower()
    if "кирпич" in lowered:
        building_type = "brick"
    elif "монолит" in lowered:
        building_type = "monolith"
    elif "панель" in lowered:
        building_type = "panel"
    elif explicit_building_type:
        building_type = explicit_building_type

    visible_city = _property_value(property_details, "Город")
    if visible_city:
        visible_city = re.sub(r"\s*показать на карте.*$", "", visible_city, flags=re.I).strip()
    city = re.split(r",", visible_city)[0].strip() if visible_city else "Астана"
    district = _property_value(property_details, "Район") or (_find_district([], visible_city) if visible_city else None) or _find_district(data, combined)
    residential_complex = _find_text(
        data,
        {"complexName", "residentialComplex", "housingComplex", "residentialComplexName"},
    )
    residential_complex = _property_value(property_details, "Жилой комплекс") or residential_complex
    if not residential_complex:
        complex_match = re.search(r"ЖК\s*[«\"]?([^»\".,;]{2,100})", combined, re.IGNORECASE)
        residential_complex = complex_match.group(1).strip() if complex_match else None
    address = _valid_property_address(_property_value(property_details, "Адрес")) or _find_address(data) or _address_from_text(combined)
    latitude = _find_coordinate(data, {"latitude", "lat", "geoLatitude", "geoLat"}, -90, 90)
    longitude = _find_coordinate(data, {"longitude", "lng", "lon", "geoLongitude", "geoLon"}, -180, 180)
    html_latitude, html_longitude = _coordinates_from_html(html)
    latitude = latitude or _find_html_coordinate(html, {"latitude", "geoLatitude", "geoLat"}, -90, 90)
    longitude = longitude or _find_html_coordinate(html, {"longitude", "geoLongitude", "geoLon"}, -180, 180)
    latitude = latitude or html_latitude
    longitude = longitude or html_longitude
    if not _is_astana_coordinate_pair(latitude, longitude):
        latitude = None
        longitude = None
    photo_urls = meta_images + _find_image_urls(data)
    photo_urls = list(dict.fromkeys(photo_urls))[:30]
    normalized_source_url = urlunparse(
        ("https", final_host or "krisha.kz", response.url.path.rstrip("/"), "", "", "")
    )
    seller_name, seller_type = _seller_fields(detail_parser, data)
    comparison_fields = _krisha_comparison_fields(detail_parser)

    return ListingCreate(
        source="krisha-import",
        source_url=HttpUrl(normalized_source_url),
        title=(title or f"{rooms}-комнатная квартира в Астане")[:300],
        description=description[:20_000] or None,
        city=city,
        district=district,
        residential_complex=residential_complex,
        address=address,
        latitude=latitude,
        longitude=longitude,
        price_kzt=price,
        area_m2=area,
        rooms=rooms,
        bathrooms_count=bathrooms_count,
        bathrooms_count_is_lower_bound=bathrooms_lower_bound,
        repair_condition=_property_value(property_details, "Состояние квартиры", "Ремонт"),
        property_details=property_details,
        details_extracted=bool(property_details or _advert_description(detail_parser)),
        floor=floor,
        floors_total=total_floors,
        building_year=year,
        building_type=building_type,
        is_full_two_room=rooms == 2,
        seller_name=seller_name,
        seller_type=seller_type,
        mortgage_supported=_mortgage_fields(html, data, description, property_details),
        photo_urls=photo_urls,
        **comparison_fields,
    )
