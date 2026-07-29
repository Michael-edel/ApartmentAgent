from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import get_settings
from app.schemas import ListingAssessment, ListingCreate


def _building_label(value: str | None) -> str:
    labels = {"brick": "кирпичный", "monolith": "монолитный", "panel": "панельный"}
    return labels.get((value or "").lower(), value or "материал не указан")


def build_local_analysis(listing: ListingCreate, assessment: ListingAssessment) -> dict[str, Any]:
    strengths: list[str] = []
    risks: list[str] = []

    if listing.rooms == 2 and listing.is_full_two_room:
        strengths.append("Формат полноценной двухкомнатной квартиры соответствует цели поиска")
    if listing.area_m2 >= 55 and listing.area_m2 <= 70:
        strengths.append(f"Площадь {listing.area_m2:g} м² попадает в целевой диапазон")
    if listing.price_kzt <= get_settings().max_price_kzt:
        strengths.append("Цена укладывается в установленный бюджет")
    if listing.building_type and any(
        word in listing.building_type.lower() for word in ("brick", "кирп", "monolith", "монолит")
    ):
        strengths.append(f"Предпочтительный материал дома: {_building_label(listing.building_type)}")
    if listing.building_year and listing.building_year >= 2018:
        strengths.append(f"Дом построен в {listing.building_year} году")
    if listing.photo_urls:
        strengths.append(f"В объявлении доступно фотографий: {len(listing.photo_urls)}")
    if listing.address or (listing.latitude is not None and listing.longitude is not None):
        strengths.append("Местоположение подтверждено данными объявления")

    if listing.floor == 1:
        risks.append("Первый этаж: нужна проверка влажности, окон и безопасности")
    if listing.floor and listing.floors_total and listing.floor == listing.floors_total:
        risks.append("Последний этаж: проверьте крышу и наличие технического этажа")
    if listing.price_kzt > get_settings().max_price_kzt:
        risks.append("Цена выше бюджета поиска")
    if not listing.district:
        risks.append("Район не определён автоматически")
    if not listing.residential_complex:
        risks.append("ЖК не определён автоматически")
    if not listing.address and (listing.latitude is None or listing.longitude is None):
        risks.append("Точный адрес и координаты не определены автоматически")
    if not listing.building_year:
        risks.append("Год постройки не указан — запросите документы")
    if not listing.building_type:
        risks.append("Материал дома не указан")
    if listing.mortgage_supported is not True:
        risks.append("Возможность покупки через Отбасы Банк не подтверждена")

    if assessment.verdict == "ПОКУПАТЬ":
        summary = "Сильный кандидат по заданным критериям; перед решением проверьте документы и состояние квартиры."
    elif assessment.verdict == "СМОТРЕТЬ":
        summary = "Кандидат требует очной проверки и уточнения недостающих характеристик."
    else:
        summary = "Объект заметно отклоняется от критериев поиска; рассматривать только при особых условиях."

    return {
        "provider": "local-rules-ai",
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "recommendation": assessment.verdict,
        "score": assessment.score,
        "confidence": min(95, 55 + len(strengths) * 7),
        "strengths": strengths or ["Положительные факторы пока не подтверждены"],
        "risks": risks or ["Критические риски по доступным данным не выявлены"],
        "next_steps": [
            "Проверить правоустанавливающие документы и обременения",
            "Сверить фактическую площадь и состояние инженерных систем",
            "Уточнить возможность ипотеки и итоговую цену сделки",
        ],
    }


def _remote_prompt(listing: ListingCreate, assessment: ListingAssessment) -> str:
    payload = {
        "title": listing.title,
        "description": listing.description,
        "city": listing.city,
        "district": listing.district,
        "residential_complex": listing.residential_complex,
        "address": listing.address,
        "latitude": listing.latitude,
        "longitude": listing.longitude,
        "price_kzt": listing.price_kzt,
        "area_m2": listing.area_m2,
        "price_per_m2": assessment.price_per_m2,
        "rooms": listing.rooms,
        "floor": listing.floor,
        "floors_total": listing.floors_total,
        "building_year": listing.building_year,
        "building_type": listing.building_type,
        "score": assessment.score,
        "verdict": assessment.verdict,
        "reasons": assessment.reasons,
    }
    return (
        "Проанализируй квартиру для покупателя в Астане. Не выдумывай отсутствующие факты. "
        "Верни только JSON с ключами summary, strengths, risks, next_steps, recommendation, confidence.\n"
        f"Данные: {json.dumps(payload, ensure_ascii=False)}"
    )


def _parse_remote_content(payload: dict[str, Any]) -> dict[str, Any] | None:
    output = payload.get("output")
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            for content in item.get("content", []) if isinstance(item, dict) else []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        if chunks:
            output = "".join(chunks)
    if not isinstance(output, str):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            output = message.get("content") if isinstance(message, dict) else None
    if not isinstance(output, str):
        return None
    try:
        result = json.loads(output.strip().strip("`"))
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict) or not isinstance(result.get("summary"), str):
        return None
    for key in ("strengths", "risks", "next_steps"):
        if not isinstance(result.get(key), list):
            result[key] = []
    result["provider"] = "remote-ai"
    result["generated_at"] = datetime.now(UTC).isoformat()
    return result


async def analyze_listing(listing: ListingCreate, assessment: ListingAssessment) -> dict[str, Any]:
    settings = get_settings()
    local = build_local_analysis(listing, assessment)
    if not settings.ai_enabled or not settings.ai_remote_enabled or not settings.openai_api_key:
        return local

    base_url = settings.openai_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    body = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": "Ты аналитик недвижимости. Отвечай только JSON."},
            {"role": "user", "content": _remote_prompt(listing, assessment)},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
            response.raise_for_status()
            remote = _parse_remote_content(response.json())
            return remote or local
    except (httpx.HTTPError, ValueError, TypeError):
        return local
