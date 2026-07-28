from __future__ import annotations

import re
from typing import Any

_PRICE_PATTERNS = (
    re.compile(r"(?P<value>\d{1,3}(?:[\s\u00a0]\d{3}){2,3})\s*(?:₸|тг|тенге)", re.IGNORECASE),
    re.compile(r"(?P<value>\d{1,2}(?:[.,]\d+)?)\s*млн", re.IGNORECASE),
)
_AREA_PATTERN = re.compile(r"(?P<value>\d{2,3}(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м)", re.IGNORECASE)
_ROOMS_PATTERN = re.compile(r"(?P<value>\d)\s*[-–]?\s*комнат", re.IGNORECASE)
_FLOOR_PATTERN = re.compile(r"(?P<floor>\d{1,2})\s*(?:из|/|этаж\s+из)\s*(?P<total>\d{1,2})", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"(?:год\s+постройки|построен\w*)\D{0,8}(?P<value>19\d{2}|20\d{2})", re.IGNORECASE)


def _parse_price(text: str) -> int | None:
    first = _PRICE_PATTERNS[0].search(text)
    if first:
        return int(re.sub(r"\D", "", first.group("value")))
    second = _PRICE_PATTERNS[1].search(text)
    if second:
        return int(float(second.group("value").replace(",", ".")) * 1_000_000)
    return None


def analyze_search_result(title: str, snippet: str | None, *, max_price: int, min_area: float, max_area: float) -> dict[str, Any]:
    text = f"{title} {snippet or ''}".replace("\u00a0", " ")
    lower = text.lower()

    price = _parse_price(text)
    area_match = _AREA_PATTERN.search(text)
    area = float(area_match.group("value").replace(",", ".")) if area_match else None
    rooms_match = _ROOMS_PATTERN.search(text)
    rooms = int(rooms_match.group("value")) if rooms_match else None
    floor_match = _FLOOR_PATTERN.search(text)
    year_match = _YEAR_PATTERN.search(text)

    floor = int(floor_match.group("floor")) if floor_match else None
    floors_total = int(floor_match.group("total")) if floor_match else None
    year = int(year_match.group("value")) if year_match else None

    score = 40
    reasons: list[str] = []

    if rooms == 2 or "двухкомнат" in lower or "2-комнат" in lower:
        score += 20
        reasons.append("двухкомнатная квартира")
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
        "area_m2": area,
        "rooms": rooms,
        "floor": floor,
        "floors_total": floors_total,
        "building_year": year,
        "price_per_m2": price_per_m2,
        "reasons": reasons,
    }
