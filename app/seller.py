"""Interpret structured seller roles without inspecting the listing description."""

from typing import Literal

SellerCategory = Literal["owner", "verified_agent", "realtor", "unknown"]
IDENTITY_CONFIRMED_REALTOR = "Специалист — личность подтверждена"

_OWNER_ROLES = {"owner", "хозяин", "хозяин недвижимости", "собственник"}
_VERIFIED_AGENT_ROLES = {"verified_agent", "крыша агент", "агент крыши", "проверенный агент крыши"}
_REALTOR_ROLES = {
    "realtor", "agent", "agency", "broker", "риелтор", "риэлтор", "специалист",
    "специалист по недвижимости", "агент по недвижимости", "агентство недвижимости",
    IDENTITY_CONFIRMED_REALTOR.casefold(),
}


def seller_category(value: str | None) -> SellerCategory:
    role = " ".join((value or "").casefold().split()).rstrip(": ")
    if role in _OWNER_ROLES:
        return "owner"
    if role in _VERIFIED_AGENT_ROLES:
        return "verified_agent"
    if role in _REALTOR_ROLES:
        return "realtor"
    return "unknown"
