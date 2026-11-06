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
from app.krisha_search import normalize_krisha_import_url


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


def _top_level_script_position(text: str, position: int) -> bool:
    quote = comment = None
    depth, escaped, index = 0, False, 0
    while index < position:
        char, pair = text[index], text[index:index + 2]
        if comment == "line":
            if char in "\r\n": comment = None
        elif comment == "block":
            if pair == "*/": comment = None; index += 1
        elif quote:
            if escaped: escaped = False
            elif char == "\\": escaped = True
            elif char == quote: quote = None
        elif pair == "//": comment = "line"; index += 1
        elif pair == "/*": comment = "block"; index += 1
        elif char in "\"'`": quote = char
        elif char in "{[(": depth += 1
        elif char in "}])": depth = max(depth - 1, 0)
        index += 1
    return quote is None and comment is None and depth == 0


def _json_scripts(html: str) -> list[Any]:
    """Read data literals only. Executable JavaScript is never evaluated."""
    values, decoder = [], json.JSONDecoder()
    assignments = re.compile(
        r"(?<![\w.$])(?:window\s*\.\s*(?:data|advert|__INITIAL_STATE__)|(?:var|let|const)\s+data)\s*=\s*",
        re.I,
    )
    for attrs, raw in re.findall(r"<script\b([^>]*)>(.*?)</script\s*>", html, re.I | re.S):
        candidates = []
        if re.search(r'''(?:type\s*=\s*["']application/(?:ld\+)?json["']|id\s*=\s*["']__NEXT_DATA__["'])''', attrs, re.I):
            candidates.append(raw.strip())
        for match in assignments.finditer(raw):
            if not _top_level_script_position(raw, match.start()):
                continue
            literal = raw[match.end():].lstrip()
            if literal.startswith(("{", "[")):
                try:
                    value, _ = decoder.raw_decode(literal)
                    values.append(value)
                except (ValueError, TypeError, RecursionError):
                    pass
        for candidate in candidates:
            try:
                values.append(json.loads(candidate))
            except (ValueError, TypeError, RecursionError):
                try:
                    values.append(json.loads(unescape(candidate)))
                except (ValueError, TypeError, RecursionError):
                    pass
    return values


def _listing_number(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    text = str(value or "").strip()
    if re.fullmatch(r"[1-9]\d{0,14}", text):
        return text
    normalized = normalize_krisha_import_url(text)
    if normalized:
        return normalized.rsplit("/", 1)[-1]
    return None


_OTHER_ADVERT_BRANCH = re.compile(r"^(?:recommendations?|similar|related|suggested|otherAdverts|recommendedAdverts)", re.I)


def _object_listing_number(item: dict[str, Any]) -> str | None:
    identities = set()
    for key in ("advertId", "advert_id", "listingId", "listing_id", "source_url", "canonicalUrl", "url", "@id"):
        if (number := _listing_number(item.get(key))) is not None:
            identities.add(number)
    kind = str(item.get("@type") or "").lower()
    advert_keys = {"price", "pricekzt", "price_kzt", "offers", "area", "area_m2", "floorsize", "rooms", "numberofrooms", "additionalproperty", "property_details"}
    if kind not in {"person", "organization", "realestateagent", "offer", "quantitativevalue", "propertyvalue"} and advert_keys.intersection(key.lower() for key in item):
        if (number := _listing_number(item.get("id"))) is not None:
            identities.add(number)
    return next(iter(identities)) if len(identities) == 1 else "!conflict" if identities else None


def _scoped_json(data: list[Any], source_url: str) -> list[dict[str, Any]]:
    """Return only objects belonging to this advert, excluding recommendation data."""
    target = _listing_number(source_url)
    if target is None:
        return []
    selected = []

    def prune(value, depth=0):
        if depth > 64:
            return None
        if isinstance(value, list):
            return [clean for child in value if (clean := prune(child, depth + 1)) is not None]
        if isinstance(value, dict):
            identity = _object_listing_number(value)
            if identity is not None and identity != target:
                return None
            return {key: clean for key, child in value.items()
                    if not _OTHER_ADVERT_BRANCH.match(key) and (clean := prune(child, depth + 1)) is not None}
        return value

    def visit(value, depth=0):
        if depth > 64:
            return
        if isinstance(value, list):
            for child in value:
                visit(child, depth + 1)
        elif isinstance(value, dict):
            identity = _object_listing_number(value)
            if identity == target:
                selected.append(prune(value))
                return
            if identity is None:
                for key, child in value.items():
                    if not _OTHER_ADVERT_BRANCH.match(key):
                        visit(child, depth + 1)

    for root in data:
        visit(root)
    return selected


_JSON_PROPERTY_LABELS = {
    "flat.toilet": "Санузел", "bathrooms": "Санузел", "bathroomsCount": "Санузел",
    "flat.renovation": "Состояние квартиры", "flat.condition": "Состояние квартиры", "repairCondition": "Состояние квартиры",
    "flat.area": "Площадь", "flat.rooms": "Количество комнат", "flat.building": "Тип дома",
    "house.type": "Тип дома", "flat.year": "Год постройки", "house.year": "Год постройки",
    "flat.floor": "Этаж", "flat.kitchen": "Площадь кухни", "flat.ceiling": "Высота потолков",
    "flat.mortgage": "Ипотека", "flat.address": "Адрес", "flat.balcony": "Балкон",
}


def _json_property_details(item: dict[str, Any]) -> dict[str, str]:
    result = {}
    for key in ("property_details", "properties", "additionalProperty", "attributes", "parameters"):
        values = item.get(key)
        if isinstance(values, dict):
            pairs = values.items()
        elif isinstance(values, list):
            pairs = ((value.get("name") or value.get("label") or value.get("title") or value.get("propertyID") or value.get("code"), value.get("value"))
                     for value in values if isinstance(value, dict))
        else:
            continue
        for label, value in pairs:
            if isinstance(value, dict):
                value = value.get("value") or value.get("name") or value.get("label")
            if not isinstance(label, str) or isinstance(value, (dict, list)) or value is None:
                continue
            label = plain_detail_text(_JSON_PROPERTY_LABELS.get(label, label)).rstrip(": ")
            value = plain_detail_text(str(value))
            if label and value and len(label) <= 100 and len(value) <= 1500 and len(result) < 60:
                result[label] = value
    return result


def _normalized_json(data: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str], bool]:
    """Flatten documented field shapes; nested seller/recommendation facts cannot leak."""
    normalized, details, rich = [], {}, False
    for item in data:
        # A selected WebPage can identify its apartment through mainEntity.
        # Inherit the already checked page identity only across this explicit
        # relation; never recursively flatten arbitrary nested objects.
        entity = item.get("mainEntity")
        if isinstance(entity, dict):
            kinds = entity.get("@type")
            kinds = kinds if isinstance(kinds, list) else [kinds]
            allowed = {"apartment", "house", "residence", "singlefamilyresidence", "accommodation", "product", "realestatelisting"}
            if any(str(kind).casefold() in allowed for kind in kinds):
                item = {**item, **entity}
        row = {}
        def first(*keys):
            return next((item[key] for key in keys if item.get(key) is not None), None)
        def scalar(value):
            if isinstance(value, dict):
                return value.get("value") if value.get("value") is not None else value.get("amount")
            return value
        aliases = {
            "price": ("price", "priceKzt", "price_kzt"), "area": ("area", "areaM2", "area_m2", "square", "floorSize", "totalArea"),
            "rooms": ("rooms", "roomCount", "numberOfRooms", "roomsCount"), "floor": ("floor", "floorNumber"),
            "floorsTotal": ("floors", "floorsTotal", "floorCount", "numberOfFloors"),
            "yearBuilt": ("year", "buildYear", "buildingYear", "yearBuilt"),
            "mortgageSupported": ("mortgageSupported", "mortgage_supported", "isMortgageAvailable"),
            "bathroomsCount": ("bathroomsCount", "bathrooms_count", "numberOfBathroomsTotal", "numberOfBathrooms"),
            "repairCondition": ("repairCondition", "repair_condition", "renovation"), "buildingType": ("buildingType", "houseType", "wallMaterial"),
            "districtName": ("district", "districtName", "areaName", "addressRegion"),
            "complexName": ("complexName", "residentialComplex", "housingComplex", "residentialComplexName"),
            "sellerName": ("sellerName", "ownerName", "advertiserName"), "sellerType": ("sellerType", "ownerType", "advertiserType"),
            "description": ("description", "descriptionText"), "title": ("title", "name", "headline"),
        }
        for target, keys in aliases.items():
            value = scalar(first(*keys))
            if value is not None and not isinstance(value, (dict, list)):
                row[target] = value
        offers = item.get("offers")
        if not row.get("price") and isinstance(offers, dict):
            if str(offers.get("priceCurrency") or "KZT").upper() == "KZT":
                row["price"] = scalar(offers.get("price"))
        if str(first("priceCurrency", "currency") or "KZT").upper() not in {"KZT", "₸", "ТГ"}:
            row.pop("price", None)
        address = first("address", "streetAddress", "formattedAddress", "fullAddress", "addressLine", "displayAddress")
        if (location := _valid_property_address(_text_value(address))):
            row["address"] = location
        city = first("city", "addressLocality")
        if isinstance(city, dict):
            city = city.get("name")
        if not city and isinstance(address, dict):
            city = address.get("addressLocality")
        if isinstance(city, str):
            row["city"] = city
        if isinstance(address, dict) and not row.get("districtName"):
            row["districtName"] = address.get("addressRegion")
        geo = item.get("geo") if isinstance(item.get("geo"), dict) else item
        for target, keys in (("latitude", ("latitude", "lat", "geoLatitude", "geoLat")), ("longitude", ("longitude", "lng", "lon", "geoLongitude", "geoLon"))):
            row[target] = next((geo[key] for key in keys if geo.get(key) is not None), None)
        seller = first("seller", "author", "owner")
        if isinstance(seller, dict):
            if not row.get("sellerName") and isinstance(seller.get("name"), str):
                row["sellerName"] = seller["name"]
            if not row.get("sellerType"):
                row["sellerType"] = seller.get("sellerType") or seller.get("role")
        for key in ("image", "images", "photo", "photos", "photoUrls", "imageUrls", "gallery", "pictures"):
            if key in item:
                row[key] = item[key]
        for key in ("krisha_district_price_per_m2", "krisha_city_price_per_m2", "krisha_comparison_district"):
            if key in item:
                row[key] = item[key]
        properties = _json_property_details(item)
        for key, value in properties.items():
            details.setdefault(key, value)
        for label, field in (("Площадь", "area"), ("Общая площадь", "area"), ("Количество комнат", "rooms"),
                             ("Год постройки", "yearBuilt"), ("Состояние квартиры", "repairCondition")):
            if not row.get(field) and properties.get(label):
                row[field] = properties[label]
        description = row.get("description")
        has_description = isinstance(description, str) and len(description.strip()) >= 30
        has_photos = any(row.get(key) for key in ("image", "images", "photos", "photoUrls", "gallery"))
        rich = rich or (len(properties) >= 3 and (has_description or bool(row.get("sellerName")))) or (
            has_description and has_photos and bool(row.get("sellerName"))
            and bool(row.get("repairCondition")) and row.get("bathroomsCount") is not None)
        normalized.append(row)
    return normalized, details, rich


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
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


def clean_listing_address(value: str | None) -> str | None:
    """Remove only a leading floor caption already mixed into an address.

    This does not geocode, guess a house number or reinterpret numeric street
    names.  A candidate consisting only of numbers/coordinates is left alone.
    """
    if not isinstance(value, str):
        return None
    address = re.sub(r"\s+", " ", value).strip()
    if not address:
        return None
    prefix = re.match(
        r"^\d{1,3}(?:\s*(?:из|/)\s*\d{1,3})?\s*(?:этаж|эт\.)\s*,\s*(?P<address>.+)$",
        address, re.IGNORECASE,
    )
    if prefix:
        candidate = prefix.group("address").strip()
        if _valid_property_address(candidate) and re.search(r"[^\W\d_]", candidate, re.UNICODE):
            return candidate
    return address


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
            r"\d+(?:[.,]\d+)?\s*м(?:²|2)\s*[·,]\s*"
            r"(?:(?:\d{1,3}(?:\s*(?:из|/)\s*\d{1,3})?\s*(?:этаж|эт\.)\s*,\s*))?"
            r"(?P<address>[^—–\n]{3,180}?)(?:\s*[—–]|$)",
            re.IGNORECASE,
        ),
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
    for index, pattern in enumerate(patterns):
        match = pattern.search(value)
        if match:
            address = clean_listing_address(match.group("address").strip(" ,.-")) or ""
            if (5 <= len(address) <= 180 and (index != 0 or re.search(r"\d", address))
                    and not re.fullmatch(r"\d{1,3}(?:\s*(?:из|/)\s*\d{1,3})?\s*(?:этаж|эт\.)", address, re.I)):
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


def _visible_coordinate_pair(parser: _BathroomFieldHTMLParser) -> tuple[float | None, float | None]:
    for node in parser.visible_nodes():
        current, unrelated = node, False
        while current is not None:
            markers = (current.attrs.get("class") or "").split()
            if current.tag == "aside" or any(re.match(r"^(?:recommend|similar|related|ad-banner)", marker, re.I) for marker in markers):
                unrelated = True
                break
            current = current.parent
        if unrelated:
            continue
        for key in ("href", "src"):
            pair = _coordinates_from_html(str(node.attrs.get(key) or ""))
            if _is_astana_coordinate_pair(*pair):
                return pair
        latitude = _coordinate(node.attrs.get("data-latitude") or node.attrs.get("data-lat"))
        longitude = _coordinate(node.attrs.get("data-longitude") or node.attrs.get("data-lng"))
        if _is_astana_coordinate_pair(latitude, longitude):
            return latitude, longitude
    return None, None


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
    source_url = normalize_krisha_import_url(source_url) or ""
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
    final_source = normalize_krisha_import_url(str(response.url))
    if final_source is None or _listing_number(final_source) != _listing_number(source_url):
        raise ListingImportError("Источник перенаправил запрос с выбранного объявления. Проверьте открытую страницу в браузере")

    html = response.text
    detail_parser, visible_details = _public_details(html)
    page_title = re.search(r"<title\b[^>]*>(.*?)</title\s*>", html, re.I | re.S)
    headings = [node.text() for node in detail_parser.visible_nodes() if node.tag == "h1"]
    titles = headings + ([unescape(page_title.group(1))] if page_title else [])
    verification = r"проверка\s+(?:безопасности|браузера)|подтвердите.{0,30}(?:человек|робот)|checking\s+your\s+browser|are\s+you\s+(?:a\s+)?robot|(?:^|\W)(?:captcha|recaptcha)(?:$|\W)|^(?:403|429|468)(?:\s*$|\s+(?:Forbidden|Too Many Requests))|HTTP(?:\s+Error)?\s*[:—-]?\s*(?:403|429|468)"
    if any(re.search(verification, value, re.I) for value in titles):
        raise ListingImportError("Источник запросил проверку безопасности. Введите данные вручную")

    raw_data = _json_scripts(html)
    scoped_data = _scoped_json(raw_data, source_url)
    data, json_details, rich_json = _normalized_json(scoped_data)
    property_details = {}
    aliases = {"ремонт": "Состояние квартиры", "в ипотеку": "Ипотека", "общая площадь": "Площадь", "санузлы": "Санузел"}
    for label, value in [*json_details.items(), *visible_details.items()]:
        property_details[aliases.get(label.casefold(), label)] = value
    title = (headings[0] if headings else None) or _extract_meta(html, "og:title") or _extract_meta(html, "twitter:title") or _find_text(data, {"title"})
    description = _extract_meta(html, "og:description") or _extract_meta(html, "description") or ""
    meta_images = [
        value
        for name in ("og:image", "twitter:image")
        if (value := _extract_meta(html, name))
    ]
    description = _advert_description(detail_parser) or _find_text(data, {"description"}) or description
    bathrooms_count, bathrooms_lower_bound = _bathrooms_fields(html, data)
    explicit_bathroom = _property_value(property_details, "Санузел", "Санузлы")
    if explicit_bathroom is not None:
        bathrooms_count, bathrooms_lower_bound = _bathroom_field_value(explicit_bathroom)

    price = _find_numeric(data, {"price", "price_kzt", "amount", "value"}, 500_000, 2_000_000_000)
    area = _find_numeric(data, {"area", "area_m2", "square", "squaremeter", "floorSize", "totalArea"}, 10, 1000)
    rooms = _find_numeric(data, {"rooms", "roomCount", "numberOfRooms", "roomsCount"}, 1, 20)
    floor = _find_numeric(data, {"floor", "floorNumber"}, 1, 200)
    total_floors = _find_numeric(data, {"floors", "floorsTotal", "floorCount", "numberOfFloors"}, 1, 200)
    year = _find_numeric(data, {"year", "buildYear", "buildingYear", "yearBuilt"}, 1900, 2100)

    # Raw script scraping can read another advert's numbers. Only visible prose
    # and an explicitly matched JSON object may supply fallbacks.
    plain_text = "" if raw_data else detail_parser.root.text()
    plain_text = re.sub(r"\s+", " ", plain_text)
    combined = " ".join(filter(None, [title, description, plain_text]))
    core_text = (title or "") if raw_data else combined

    price_match = _PRICE_RE.search(core_text)
    area_match = _AREA_RE.search(core_text)
    rooms_match = _ROOMS_RE.search(core_text)
    floor_match = _FLOOR_RE.search(core_text)
    year_match = _YEAR_RE.search(core_text)

    price = int(price or (_clean_number(price_match.group("price")) if price_match else 0)) or None
    area = float(area or (area_match.group("area").replace(",", ".") if area_match else 0)) or None
    rooms = int(rooms or (rooms_match.group("rooms") if rooms_match else 0)) or None
    floor = int(floor or (floor_match.group("floor") if floor_match else 0)) or None
    total_floors = int(total_floors or (floor_match.group("total") if floor_match else 0)) or None
    year = int(year or (year_match.group("year") if year_match else 0)) or None
    headline = headings[0] if headings else ""
    if match := _AREA_RE.search(headline):
        area = float(match.group("area").replace(",", "."))
    if match := _ROOMS_RE.search(headline):
        rooms = int(match.group("rooms"))
    if match := re.search(r"(\d{1,3})\s*(?:из|/)\s*(\d{1,3})\s*(?:этаж|эт\.)", headline, re.I):
        floor, total_floors = int(match.group(1)), int(match.group(2))
    visible_price = _number(_property_value(visible_details, "Цена", "Стоимость"))
    if visible_price is not None and 500_000 <= visible_price <= 2_000_000_000:
        price = int(visible_price)
    for node in detail_parser.visible_nodes():
        if ("offer__price" in (node.attrs.get("class") or "").split()
                or node.attrs.get("data-testid") == "price" or node.attrs.get("itemprop") == "price"):
            visible_price = _number(node.attrs.get("content") or node.text())
            if visible_price is not None and 500_000 <= visible_price <= 2_000_000_000:
                price = int(visible_price)
                break
    visible_area = _property_value(visible_details, "Площадь", "Общая площадь")
    if visible_area and (match := re.match(r"^(\d+(?:[.,]\d+)?)", visible_area)):
        area = float(match.group(1).replace(",", "."))
    visible_rooms = _property_value(visible_details, "Количество комнат", "Комнат")
    if visible_rooms and (match := re.match(r"^(\d+)", visible_rooms)):
        rooms = int(match.group(1))
    visible_floor = _property_value(property_details, "Этаж")
    if visible_floor and (match := re.fullmatch(r"(\d{1,3})\s*(?:из|/)\s*(\d{1,3})(?:\s*этаж)?", visible_floor, re.I)):
        floor, total_floors = int(match.group(1)), int(match.group(2))
    visible_year = _property_value(property_details, "Год постройки")
    if visible_year and re.fullmatch(r"(?:19|20)\d{2}", visible_year):
        year = int(visible_year)
    if floor and total_floors and floor > total_floors:
        floor = total_floors = None

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
    explicit_building_type = _property_value(property_details, "Тип дома", "Материал стен") or _find_text(data, {"buildingType"})
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
    city = re.split(r",", visible_city)[0].strip() if visible_city else (_find_text(data, {"city"}) or "Астана")
    district = _property_value(property_details, "Район") or (_find_district([], visible_city) if visible_city else None) or _find_district(data, combined)
    residential_complex = _find_text(
        data,
        {"complexName", "residentialComplex", "housingComplex", "residentialComplexName"},
    )
    residential_complex = _property_value(property_details, "Жилой комплекс") or residential_complex
    if not residential_complex:
        complex_match = re.search(r"ЖК\s*[«\"]?([^»\".,;]{2,100})", combined, re.IGNORECASE)
        residential_complex = complex_match.group(1).strip() if complex_match else None
    headline_address = _address_from_text(headline)
    if headline_address and not re.search(r"\d", headline_address):
        headline_address = None
    address = (_valid_property_address(_property_value(visible_details, "Адрес"))
               or headline_address
               or _valid_property_address(_property_value(json_details, "Адрес"))
               or _find_address(data) or _address_from_text(combined))
    # Coordinates form an indivisible observation. Never combine one coordinate
    # from one JSON object with another object or a later DOM observation.
    latitude, longitude = _visible_coordinate_pair(detail_parser)
    if not _is_astana_coordinate_pair(latitude, longitude):
        latitude = longitude = None
        for row in data:
            pair = (_find_coordinate([row], {"latitude"}, -90, 90),
                    _find_coordinate([row], {"longitude"}, -180, 180))
            if _is_astana_coordinate_pair(*pair):
                latitude, longitude = pair
                break
    photo_urls = meta_images + _find_image_urls(data)
    photo_urls = [url for url in dict.fromkeys(photo_urls)
                  if not re.search(r"/static/frontend/images/empty-photo-[^/?#]*", url, re.I)][:30]
    normalized_source_url = urlunparse(
        ("https", final_host or "krisha.kz", response.url.path.rstrip("/"), "", "", "")
    )
    seller_name, seller_type = _seller_fields(detail_parser, data)
    comparison_fields = _krisha_comparison_fields(detail_parser)
    for key in ("krisha_district_price_per_m2", "krisha_city_price_per_m2"):
        if comparison_fields[key] is None:
            value = _find_numeric(data, {key}, 1, 1_000_000_000)
            comparison_fields[key] = int(value) if value is not None else None
    comparison_fields["krisha_comparison_district"] = comparison_fields["krisha_comparison_district"] or _find_text(data, {"krisha_comparison_district"})

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
        repair_condition=_property_value(property_details, "Состояние квартиры", "Ремонт") or _find_text(data, {"repairCondition"}),
        property_details=property_details,
        details_extracted=bool(visible_details or _advert_description(detail_parser) or rich_json),
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
