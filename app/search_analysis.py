from __future__ import annotations

import re
from typing import Any

_PRICE_PATTERNS = (
    re.compile(r"(?<!\d)(?P<value>(?:\d{1,3}(?:[\s\u00a0]\d{3}){2,3}|\d{6,11}))\s*(?:₸|тг|тенге)", re.IGNORECASE),
    re.compile(r"(?<![\d.,])(?P<value>\d{1,5}(?:[.,]\d+)?)\s*млн", re.IGNORECASE),
)
_AREA_PATTERN = re.compile(r"(?<![\d.,])(?P<value>\d{1,5}(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м)", re.IGNORECASE)
_ROOMS_PATTERN = re.compile(r"(?P<value>\d)\s*[-–]?\s*комнат", re.IGNORECASE)
_FLOOR_PATTERN = re.compile(r"(?P<floor>\d{1,2})\s*(?:из|/|этаж\s+из)\s*(?P<total>\d{1,2})", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"(?:год\s+постройки|построен\w*)\D{0,8}(?P<value>19\d{2}|20\d{2})", re.IGNORECASE)
_BATHROOM_PATTERN = re.compile(r"(?<![\d.,])(?P<before>\d{1,2})\s*(?:с\s*/\s*у|сануз(?:ел|л(?:а|ов)?))|(?:сануз(?:ел|л(?:а|ов)?)|с\s*/\s*у)\s*[:—-]\s*(?P<after>\d{1,2})(?![\d.,])", re.IGNORECASE)


def parse_bathrooms_count(text: str) -> int | None:
    match = _BATHROOM_PATTERN.search(text)
    if not match:
        return None
    count = int(match.group('before') or match.group('after'))
    return count if count <= 20 else None


def bathrooms_is_lower_bound(text: str) -> bool:
    match = _BATHROOM_PATTERN.search(text)
    return bool(match and re.match(r"\s*(?:с\s*/\s*у\s*)?(?:и\s+более|и\s+больше|\+)", text[match.end():], re.IGNORECASE))


def _parse_price(text: str) -> tuple[int | None, bool]:
    first = _PRICE_PATTERNS[0].search(text)
    if first:
        value = int(re.sub(r"\D", "", first.group("value")))
        return value, bool(re.search(r"\bот\s*$", text[:first.start()], re.IGNORECASE))
    second = _PRICE_PATTERNS[1].search(text)
    if second:
        value = int(float(second.group("value").replace(",", ".")) * 1_000_000)
        return value, bool(re.search(r"\bот\s*$", text[:second.start()], re.IGNORECASE))
    return None, False


def analyze_search_result(title: str, snippet: str | None, *, max_price: int, min_area: float, max_area: float, target_rooms: int = 2) -> dict[str, Any]:
    text = f"{title} {snippet or ''}".replace("\u00a0", " ")
    lower = text.lower()

    parsed_price, price_is_lower_bound = _parse_price(text)
    price = None if price_is_lower_bound else parsed_price
    price_lower_bound = parsed_price if price_is_lower_bound else None
    area_match = _AREA_PATTERN.search(text)
    area = float(area_match.group("value").replace(",", ".")) if area_match else None
    rooms_match = _ROOMS_PATTERN.search(text)
    rooms = int(rooms_match.group("value")) if rooms_match else None
    if rooms is None:
        rooms = next((count for word, count in (("двухкомнат", 2), ("трехкомнат", 3), ("трёхкомнат", 3), ("четырехкомнат", 4), ("четырёхкомнат", 4)) if word in lower), None)
    floor_match = _FLOOR_PATTERN.search(text)
    year_match = _YEAR_PATTERN.search(text)

    floor = int(floor_match.group("floor")) if floor_match else None
    floors_total = int(floor_match.group("total")) if floor_match else None
    year = int(year_match.group("value")) if year_match else None

    score = 40
    reasons: list[str] = []

    if rooms == target_rooms:
        score += 20
        reasons.append(f"квартира: {rooms} комнат")
    elif rooms is not None:
        score -= 20
        reasons.append(f"указано комнат: {rooms}")

    if area is not None:
        if min_area <= area <= max_area:
            score += 20
            reasons.append("площадь соответствует критериям")
        else:
            score -= 10
            reasons.append("площадь вне заданного диапазона")

    if price is not None:
        if price <= max_price:
            score += 15
            reasons.append("цена в пределах бюджета")
        else:
            score -= 15
            reasons.append("цена выше бюджета")
    elif price_lower_bound is not None:
        if price_lower_bound > max_price:
            score -= 15
            reasons.append("минимальная цена выше бюджета")
        else:
            reasons.append("указана только нижняя граница цены; бюджет не подтверждён")

    if floor == 1:
        score -= 10
        reasons.append("первый этаж")
    elif floor is not None and floors_total is not None and floor == floors_total:
        score -= 8
        reasons.append("последний этаж")

    if "кирпич" in lower or "монолит" in lower:
        score += 5
        reasons.append("предпочтительный материал дома")

    score = max(0, min(100, score))
    if score >= 85:
        verdict = "СРОЧНО ПОСМОТРЕТЬ"
    elif score >= 65:
        verdict = "ПРОВЕРИТЬ"
    else:
        verdict = "НИЗКИЙ ПРИОРИТЕТ"

    price_per_m2 = round(price / area) if price and area else None
    return {
        "score": score,
        "verdict": verdict,
        "price_kzt": price,
        "price_is_lower_bound": price_is_lower_bound,
        "price_lower_bound_kzt": price_lower_bound,
        "area_m2": area,
        "rooms": rooms,
        "bathrooms_count": parse_bathrooms_count(text),
        "bathrooms_count_is_lower_bound": bathrooms_is_lower_bound(text),
        "floor": floor,
        "floors_total": floors_total,
        "building_year": year,
        "price_per_m2": price_per_m2,
        "reasons": reasons,
    }


def is_target_search_result(
    title: str,
    snippet: str | None,
    *,
    max_price: int,
    min_area: float,
    max_area: float,
    target_rooms: int = 2,
) -> bool:
    """Пропускает квартиры с указанной комнатностью и площадью в заданном диапазоне."""
    analysis = analyze_search_result(
        title,
        snippet,
        max_price=max_price,
        min_area=min_area,
        max_area=max_area,
        target_rooms=target_rooms,
    )

    rooms = analysis["rooms"]
    if rooms != target_rooms:
        return False

    area = analysis["area_m2"]
    if area is None or not (min_area <= area <= max_area):
        return False

    price = analysis["price_kzt"] or analysis["price_lower_bound_kzt"]
    return not (price is not None and price > max_price)
