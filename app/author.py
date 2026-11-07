"""Read an explicit owner claim without guessing the seller's name."""

import re


OWNER_FROM_DESCRIPTION = "Собственник (по описанию)"
_OWNER_CLAIM = re.compile(
    r"^(?:прямая\s+продажа\s+от\s+(?:хозяев|хозяина|собственника)"
    r"|я\s+(?:—\s*)?собственник\s+(?:этой\s+)?квартиры)"
    r"(?=$|\s*[,.;!—–-])",
    re.I,
)
_CONFLICT = re.compile(
    r"\b(?:я\s+ри[еэ]лтор|мы\s+(?:ри[еэ]лторы|агентство)|"
    r"(?:не|якобы)\s+собственник|от\s+имени\s+собственника)\b",
    re.I,
)


def owner_type_from_description(description: str | None) -> str | None:
    if not description or _CONFLICT.search(description):
        return None
    for unit in re.split(r"\n+|(?<=[.!;])\s+", description[:20_000]):
        if "?" not in unit and _OWNER_CLAIM.match(unit.strip(" \t*•✔✅—–-")):
            return OWNER_FROM_DESCRIPTION
    return None
