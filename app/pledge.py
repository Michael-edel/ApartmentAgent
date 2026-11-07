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
    claims: dict[bool, str] = {}
    for unit in re.split(r"(?<=[.!?;])\s+|[\r\n]+", (description or "")[:20_000]):
        match = _CLAIM.match(unit)
        if match is None or "?" in unit or _UNCERTAIN_TAIL.match(unit[match.end():]):
            continue
        status = not (match.group("not_located") or match.group("not_pledged"))
        claims.setdefault(status, match.group().strip())
    return next(iter(claims.items())) if len(claims) == 1 else None
