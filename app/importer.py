import json
import re
from html import unescape
from urllib.parse import urlparse

import httpx

from app.schemas import ListingCreate

_ALLOWED_HOSTS = {"krisha.kz", "www.krisha.kz"}
_PRICE_RE = re.compile(r"(?P<price>\d[\d\s\u00a0]{5,})\s*(?:₸|тг|тенге)", re.IGNORECASE)
_AREA_RE = re.compile(r"(?P<area>\d{2,3}(?:[.,]\d+)?)\s*м(?:²|2)", re.IGNORECASE)
_ROOMS_RE = re.compile(r"(?P<rooms>\d+)\s*[- ]?комнат", re.IGNORECASE)
_FLOOR_RE = re.compile(r"(?P<floor>\d+)\s*(?:этаж|эт\.)\s*(?:из|/)\s*(?P<total>\d+)", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?:год постройки|построен(?:а|о)? в)\D{0,12}(?P<year>19\d{2}|20\d{2})", re.IGNORECASE)


class ListingImportError(ValueError):
    pass


def _clean_number(value: str) -> int:
    return int(re.sub(r"\D", "", value))


def _extract_meta(html: str, property_name: str) -> str | None:
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(property_name)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return unescape(match.group(1)).strip()
    return None


def _extract_json_ld(html: str) -> list[dict]:
    result: list[dict] = []
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            value = json.loads(unescape(raw).strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            result.append(value)
        elif isinstance(value, list):
            result.extend(item for item in value if isinstance(item, dict))
    return result


def _first_text(*values: str | None) -> str:
    return " ".join(value for value in values if value)


async def import_krisha_listing(source_url: str) -> ListingCreate:
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in _ALLOWED_HOSTS:
        raise ListingImportError("Разрешены только ссылки krisha.kz")

    headers = {
        "User-Agent": "ApartmentAgent/0.3 (+personal listing analysis)",
        "Accept-Language": "ru-KZ,ru;q=0.9",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, headers=headers) as client:
            response = await client.get(source_url)
    except httpx.HTTPError as exc:
        raise ListingImportError("Не удалось загрузить объявление") from exc

    if response.status_code in {301, 302, 303, 307, 308}:
        raise ListingImportError("Источник вернул перенаправление. Откройте конечную ссылку и вставьте её снова")
    if response.status_code in {403, 429}:
        raise ListingImportError("Источник ограничил автоматический доступ. Введите данные вручную")
    if response.status_code != 200:
        raise ListingImportError(f"Источник вернул HTTP {response.status_code}")

    html = response.text
    title = _extract_meta(html, "og:title") or "2-комнатная квартира в Астане"
    description = _extract_meta(html, "og:description") or ""
    json_ld = _extract_json_ld(html)
    text = _first_text(title, description, re.sub(r"<[^>]+>", " ", html[:400_000]))

    price: int | None = None
    area: float | None = None
    rooms: int | None = None
    for item in json_ld:
        offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
        raw_price = offers.get("price") or item.get("price")
        floor_size = item.get("floorSize") if isinstance(item.get("floorSize"), dict) else {}
        raw_area = floor_size.get("value") or item.get("area")
        raw_rooms = item.get("numberOfRooms")
        try:
            if raw_price and not price:
                price = int(float(str(raw_price).replace(" ", "")))
            if raw_area and not area:
                area = float(str(raw_area).replace(",", "."))
            if raw_rooms and not rooms:
                rooms = int(float(str(raw_rooms)))
        except ValueError:
            pass

    price_match = _PRICE_RE.search(text)
    area_match = _AREA_RE.search(text)
    rooms_match = _ROOMS_RE.search(text)
    floor_match = _FLOOR_RE.search(text)
    year_match = _YEAR_RE.search(text)

    price = price or (_clean_number(price_match.group("price")) if price_match else None)
    area = area or (float(area_match.group("area").replace(",", ".")) if area_match else None)
    rooms = rooms or (int(rooms_match.group("rooms")) if rooms_match else None)

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
    lowered = text.lower()
    if "кирпич" in lowered:
        building_type = "brick"
    elif "монолит" in lowered:
        building_type = "monolith"
    elif "панель" in lowered:
        building_type = "panel"

    return ListingCreate(
        source="krisha-import",
        source_url=source_url,
        title=title[:300],
        city="Астана",
        price_kzt=price,
        area_m2=area,
        rooms=rooms,
        floor=int(floor_match.group("floor")) if floor_match else None,
        floors_total=int(floor_match.group("total")) if floor_match else None,
        building_year=int(year_match.group("year")) if year_match else None,
        building_type=building_type,
        is_full_two_room=rooms == 2,
        mortgage_supported=None,
    )
