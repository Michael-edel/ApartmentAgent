import json
import re
from html import unescape
from typing import Any
from urllib.parse import unquote, urlparse, urlunparse

import httpx
from pydantic import HttpUrl

from app.schemas import ListingCreate

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


def _find_district(data: list[Any], combined: str) -> str | None:
    district = _find_text(data, {"district", "districtName", "areaName"})
    if not district:
        region = _find_text(data, {"regionName", "addressRegion"})
        if region and re.search(r"\b(?:район|р-н)\b", region, re.IGNORECASE):
            district = region
    if not district:
        district_match = re.search(
            r"((?:Есильский|Нура|Алматы|Сарыарка|Байконур)\s+(?:район|р-н))",
            combined,
            re.IGNORECASE,
        )
        district = district_match.group(1) if district_match else None
    return re.sub(r"\s+р-н\b", " район", district, flags=re.IGNORECASE) if district else None


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
    if response.status_code in {403, 429}:
        raise ListingImportError("Источник ограничил автоматический доступ. Введите данные вручную")
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
    lowered = combined.lower()
    if "кирпич" in lowered:
        building_type = "brick"
    elif "монолит" in lowered:
        building_type = "monolith"
    elif "панель" in lowered:
        building_type = "panel"

    district = _find_district(data, combined)
    residential_complex = _find_text(
        data,
        {"complexName", "residentialComplex", "housingComplex", "residentialComplexName"},
    )
    if not residential_complex:
        complex_match = re.search(r"ЖК\s*[«\"]?([^»\".,;]{2,100})", combined, re.IGNORECASE)
        residential_complex = complex_match.group(1).strip() if complex_match else None
    address = _find_text(
        data,
        {"address", "streetAddress", "formattedAddress", "fullAddress", "addressLine", "displayAddress"},
    ) or _address_from_text(combined)
    latitude = _find_coordinate(data, {"latitude", "lat", "geoLatitude", "geoLat"}, -90, 90)
    longitude = _find_coordinate(data, {"longitude", "lng", "lon", "geoLongitude", "geoLon"}, -180, 180)
    html_latitude, html_longitude = _coordinates_from_html(html)
    latitude = latitude or _find_html_coordinate(html, {"latitude", "geoLatitude", "geoLat"}, -90, 90)
    longitude = longitude or _find_html_coordinate(html, {"longitude", "geoLongitude", "geoLon"}, -180, 180)
    latitude = latitude or html_latitude
    longitude = longitude or html_longitude
    photo_urls = meta_images + _find_image_urls(data)
    photo_urls = list(dict.fromkeys(photo_urls))[:30]
    normalized_source_url = urlunparse(
        ("https", final_host or "krisha.kz", response.url.path.rstrip("/"), "", "", "")
    )

    return ListingCreate(
        source="krisha-import",
        source_url=HttpUrl(normalized_source_url),
        title=(title or f"{rooms}-комнатная квартира в Астане")[:300],
        description=description[:20_000] or None,
        city="Астана",
        district=district,
        residential_complex=residential_complex,
        address=address,
        latitude=latitude,
        longitude=longitude,
        price_kzt=price,
        area_m2=area,
        rooms=rooms,
        floor=floor,
        floors_total=total_floors,
        building_year=year,
        building_type=building_type,
        is_full_two_room=rooms == 2,
        mortgage_supported=None,
        photo_urls=photo_urls,
    )
