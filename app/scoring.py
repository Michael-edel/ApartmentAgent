from typing import Literal

from app.config import Settings
from app.astana_bank import astana_bank
from app.cities import CITY_NAMES, city_slug
from app.schemas import ListingAssessment, ListingCreate
from app.search_analysis import is_area_in_range
from app.search_conditions import evaluate_conditions, kitchen_area


def listing_river_bank(city: str | None, latitude: float | None, longitude: float | None) -> Literal['left', 'right', 'unknown']:
    if city_slug(city) != 'astana':
        return 'unknown'
    return astana_bank(latitude, longitude)


def assess_listing(listing: ListingCreate, settings: Settings) -> ListingAssessment:
    score = 100
    reasons: list[str] = []
    condition_result = evaluate_conditions(settings.search_conditions, floor=listing.floor,
        floors_total=listing.floors_total, building_year=listing.building_year,
        kitchen_m2=kitchen_area(listing.property_details))
    score += condition_result['score_adjustment']
    reasons.extend(condition_result['reasons'])
    if condition_result['unknown']:
        reasons.append('Не подтверждены условия: ' + ', '.join(condition_result['unknown']))

    if settings.min_bathrooms:
        if listing.bathrooms_count is None:
            score -= 15
            reasons.append("Количество санузлов не подтверждено; требуется проверка")
        elif listing.bathrooms_count < settings.min_bathrooms:
            score -= 35
            reasons.append(f"Санузлов меньше установленного минимума: {settings.min_bathrooms}")

    if city_slug(listing.city) != settings.search_city:
        score -= 40
        reasons.append(f"Объект находится вне выбранного города ({CITY_NAMES[settings.search_city]})")

    if listing.rooms < settings.target_rooms or (listing.rooms == 2 and settings.target_rooms == 2 and not listing.is_full_two_room):
        score -= 35
        reasons.append(f"Не соответствует критерию: минимум {settings.target_rooms} комнат")

    if settings.min_price_kzt is not None and listing.price_kzt < settings.min_price_kzt:
        score -= 15
        reasons.append("Цена ниже установленного минимума")

    if listing.price_kzt > settings.max_price_kzt:
        over_percent = (listing.price_kzt / settings.max_price_kzt - 1) * 100
        score -= min(30, round(over_percent) + 10)
        reasons.append("Цена выше установленного бюджета")

    if not is_area_in_range(listing.area_m2, settings.min_area_m2, settings.max_area_m2):
        score -= 20
        if settings.max_area_m2 is None:
            reasons.append(f"Площадь меньше минимальной: {settings.min_area_m2:g} м²")
        else:
            reasons.append(f"Площадь вне целевого диапазона {settings.min_area_m2:g}–{settings.max_area_m2:g} м²")

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
        reasons.append("В объявлении указано: ипотека недоступна")
    if listing.is_pledged is True:
        score -= 25
        reasons.append("В залоге")

    if listing.twogis_rating is not None:
        review_count = listing.twogis_review_count or 0
        if review_count >= 5 and listing.twogis_rating >= 4.5:
            score += 4
            reasons.append(
                f"Высокая оценка ЖК в 2GIS: {listing.twogis_rating:.1f}/5 ({review_count} отзывов)"
            )
        elif review_count >= 5 and listing.twogis_rating >= 4:
            score += 2
            reasons.append(
                f"Хорошая оценка ЖК в 2GIS: {listing.twogis_rating:.1f}/5 ({review_count} отзывов)"
            )
        elif review_count >= 5 and listing.twogis_rating < 3:
            score -= 8
            reasons.append(
                f"Низкая оценка ЖК в 2GIS: {listing.twogis_rating:.1f}/5 ({review_count} отзывов)"
            )
        elif review_count >= 5 and listing.twogis_rating < 3.5:
            score -= 4
            reasons.append(
                f"Сдержанная оценка ЖК в 2GIS: {listing.twogis_rating:.1f}/5 ({review_count} отзывов)"
            )
        elif review_count:
            reasons.append(
                f"Оценка ЖК в 2GIS: {listing.twogis_rating:.1f}/5 ({review_count} отзывов)"
            )

    if listing_river_bank(listing.city, listing.latitude, listing.longitude) == 'left':
        score += 5
        reasons.append('Левый берег по координатам: +5')
    score = max(0, min(100, score))
    if settings.min_bathrooms and listing.bathrooms_count is None:
        score = min(score, 89)
    elif settings.min_bathrooms and listing.bathrooms_count < settings.min_bathrooms:
        score = min(score, 74)
    price_per_m2 = round(listing.price_kzt / listing.area_m2)

    verdict: Literal["ПОКУПАТЬ", "СМОТРЕТЬ", "НЕ РЕКОМЕНДУЮ"]
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
