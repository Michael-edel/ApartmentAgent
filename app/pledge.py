"""Read explicit pledge status from an already normalized advertisement."""

import re


_YES = {"да", "true", "yes", "иә", "в залоге"}
_NO = {"нет", "false", "no", "жоқ", "жок", "не в залоге"}
_CLAIM = re.compile(
    r"^[\s•*✔✅☑️📄🏠—–-]*(?:(?:прода[её]тся|продам)\s+)?"
    r"(?:(?:эта|данная|наша|эту|данную|нашу)\s+)?(?:квартир[ау]|объект)\s+"
    r"(?:(?:сейчас|на\s+данный\s+момент)\s+)?(?P<not_located>не\s+)?"
    r"(?:находится\s+)?(?P<not_pledged>не\s+)?в\s+залоге\b",
    re.I,
)
_UNCERTAIN_TAIL = re.compile(
    r"^\s*[:,—–-]?\s*(?:ли|или|либо|если|возможно|вероятно|наверное|"
    r"будет|будут|был[аои]?|раньше|ранее|после|не\s+(?:был[аои]?|будет|находится))\b",
    re.I,
)
_BADGE = re.compile(
    r"^[^\S\r\n]*о[^\S\r\n]+квартире[^\S\r\n]*:?[^\S\r\n]*\r?\n"
    r"[^\S\r\n]*(?P<badge>в[^\S\r\n]+залоге)[^\S\r\n]*\r?\n"
    r"[^\S\r\n]*(?:балкон(?: остекл[её]н)?|дверь|санузел|телефон|интернет|пол|парковка|"
    r"квартира меблирована|высота потолков|возможен обмен|бывшее общежитие)[^\S\r\n]*\r?\n"
    r"[^\S\r\n]*(?P<value>\S[^\r\n]*)",
    re.I | re.M,
)
_BADGE_REFUSAL = re.compile(
    r"^[^\S\r\n]*в[^\S\r\n]+залоге[^\S\r\n]*(?:[:—–-]|\r?\n)[^\S\r\n]*"
    rf"(?:{'|'.join(sorted(_NO))})\.?[^\S\r\n]*(?=\r?$)",
    re.I | re.M,
)
_EMPTY_PROPERTY = {"-", "—", "?", "не указано", "нет данных"}


def pledge_field(details: dict[str, str]) -> bool | None:
    for label, value in details.items():
        if " ".join(label.casefold().split()).rstrip(":").rstrip() != "в залоге":
            continue
        normalized = " ".join(value.casefold().split()).rstrip(".")
        if normalized in _YES:
            return True
        if normalized in _NO:
            return False
    return None


def parse_pledge_claim(description: str | None) -> tuple[bool, str] | None:
    """Return a direct current-apartment claim and a verbatim excerpt."""
    description = (description or "")[:20_000]
    claims: dict[bool, str] = {}
    for unit in re.split(r"(?<=[.!?;])\s+|[\r\n]+", description):
        match = _CLAIM.match(unit)
        if match is None or "?" in unit or _UNCERTAIN_TAIL.match(unit[match.end():]):
            continue
        status = not (match.group("not_located") or match.group("not_pledged"))
        claims.setdefault(status, match.group().strip())
    badge = _BADGE.search(description)
    if (badge is not None and badge.group("value").strip().casefold() not in _EMPTY_PROPERTY
            and not _BADGE_REFUSAL.search(description)):
        claims.setdefault(True, badge.group("badge"))
    return next(iter(claims.items())) if len(claims) == 1 else None
