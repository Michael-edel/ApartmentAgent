from app.config import Settings
from app.schemas import ListingAssessment, ListingCreate


def assess_listing(listing: ListingCreate, settings: Settings) -> ListingAssessment:
    score = 100
    reasons: list[str] = []

    if listing.city.strip().lower() not in {"астана", "astana"}:
        score -= 40
        reasons.append("Объект находится не в Астане")

    if listing.rooms != 2 or not listing.is_full_two_room:
        score -= 35
        reasons.append("Не является полноценной двухкомнатной квартирой")

    if listing.price_kzt > settings.max_price_kzt:
        over_percent = (listing.price_kzt / settings.max_price_kzt - 1) * 100
        score -= min(30, round(over_percent) + 10)
        reasons.append("Цена выше установленного бюджета")

    if not settings.min_area_m2 <= listing.area_m2 <= settings.max_area_m2:
        score -= 20
        reasons.append("Площадь вне целевого диапазона 55–70 м²")

    if listing.floor == 1:
        score -= 15
        reasons.append("Первый этаж")

    if listing.floor and listing.floors_total and listing.floor == listing.floors_total:
        score -= 12
        reasons.append("Последний этаж требует проверки технического этажа и крыши")

    if listing.building_type:
        building_type = listing.building_type.lower()
        if "кирп" in building_type or "монолит" in building_type:
            reasons.append("Предпочтительный тип дома")
        elif "панел" in building_type:
            score -= 8
            reasons.append("Панельный дом требует усиленной проверки тепло- и шумоизоляции")

    if listing.building_year and listing.building_year < 2010:
        score -= 8
        reasons.append("Дом старше 2010 года")
    elif listing.building_year and listing.building_year >= 2018:
        score += 3
        reasons.append("Современный год постройки")

    if listing.mortgage_supported is False:
        score -= 25
        reasons.append("Не подтверждена возможность покупки через ипотеку")

    score = max(0, min(100, score))
    price_per_m2 = round(listing.price_kzt / listing.area_m2)

    if score >= 90:
        verdict = "ПОКУПАТЬ"
    elif score >= 75:
        verdict = "СМОТРЕТЬ"
    else:
        verdict = "НЕ РЕКОМЕНДУЮ"

    if not reasons:
        reasons.append("Объект соответствует базовым фильтрам")

    return ListingAssessment(
        price_per_m2=price_per_m2,
        score=score,
        verdict=verdict,
        reasons=reasons,
    )
