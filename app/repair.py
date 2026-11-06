"""Conservative repair claims from an already imported advert description.

These are the seller's statements, not an inspection or an assessment.  Callers
must keep the returned evidence and must prefer an explicit structured field.
"""
from __future__ import annotations

import re


_SPACE = r"[^\S\r\n]+"
_REPAIR = r"ремонт(?:а|ом|е|у)?"
_PATTERNS = (
    ("предчистовая отделка", rf"пред[^\S\r\n-]*-?[^\S\r\n]*чистов(?:ая|ой|ую|ое){_SPACE}отделк[а-яё]*"),
    ("черновая отделка", rf"чернов(?:ая|ой|ую|ое){_SPACE}отделк[а-яё]*"),
    ("чистовая отделка", rf"чистов(?:ая|ой|ую|ое){_SPACE}отделк[а-яё]*"),
    ("без отделки", rf"без{_SPACE}отделки"),
    ("без ремонта", rf"без{_SPACE}ремонта"),
    ("требуется ремонт", rf"(?:требуется|нужен|необходим){_SPACE}(?:(?:косметический|капитальный){_SPACE})?ремонт|требует{_SPACE}ремонта|нуждается{_SPACE}в{_SPACE}(?:(?:косметическом|капитальном|хорошем){_SPACE})?ремонте"),
    ("не новый, но аккуратный ремонт", rf"не{_SPACE}нов(?:ый|ым|ого|ом)\s*,?{_SPACE}но{_SPACE}аккуратн(?:ый|ым|ого|ом){_SPACE}{_REPAIR}"),
    ("хороший ремонт", rf"хорош(?:ий|им|его|ем){_SPACE}{_REPAIR}"),
    ("качественный ремонт", rf"качественн(?:ый|ым|ого|ом){_SPACE}{_REPAIR}"),
    ("свежий ремонт", rf"свеж(?:ий|им|его|ем){_SPACE}{_REPAIR}"),
    ("новый ремонт", rf"нов(?:ый|ым|ого|ом){_SPACE}{_REPAIR}"),
    ("средний ремонт", rf"средн(?:ий|им|его|ем){_SPACE}{_REPAIR}"),
    ("косметический ремонт", rf"косметическ(?:ий|им|ого|ом){_SPACE}{_REPAIR}"),
    ("дизайнерский ремонт", rf"дизайнерск(?:ий|им|ого|ом){_SPACE}{_REPAIR}"),
    ("евроремонт", r"евро[^\S\r\n-]*-?[^\S\r\n]*ремонт(?:а|ом|е|у)?"),
    ("хорошее состояние", rf"хорош(?:ее|ем){_SPACE}состояни[еи]"),
)
_COMPILED = tuple((condition, re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.I))
                  for condition, pattern in _PATTERNS)
_APARTMENT = re.compile(r"\b(?:квартир(?:а|у|е|ой)|жиль[еёя]|апартамент(?:ы|ах)?|студи(?:я|ю|и))\b", re.I)
_NON_APARTMENT = re.compile(r"\b(?:подъезд\w*|парадн\w*|фасад\w*|крыш\w*|кровл\w*|холл\w*|лестниц\w*|сосед\w*|офис\w*|паркинг\w*|лифтов\w*|ванн\w*|санузл\w*|кухн\w*|балкон\w*|спальн\w*|дом(?:а|е|ом|ов)?|здани\w*|гараж\w*)\b", re.I)
_GENERIC_COMPLEX = re.compile(r"\b(?:жк|жил(?:ой|ого|ом)\s+комплекс\w*)\b", re.I)
_HYPOTHETICAL = re.compile(r"\b(?:можно|можете|сможете|хотите|планиру\w*|предстоит|понадобится|сдела(?:ть|ем|ете)|выполним|закаж\w*|обновим|будет|будут)\b|\b(?:при|по)\s+желанию\b|\bза\s+(?:наш|ваш)\s+сч[её]т\b|\b(?:услуги|дизайн[ -]проект)\b", re.I)
_NEGATED_BEFORE = re.compile(r"\b(?:не\s+(?:с\s+)?|нет\s+|отсутствует\s+)$", re.I)
_NEGATED_AFTER = re.compile(r"^\s+не\s+(?:сделан\w*|выполнен\w*|нужен\w*|требуется|делали)\b", re.I)
_NEEDED_BEFORE = re.compile(r"\b(?:требуется|нужен|необходим|нуждается\s+в)\s+$", re.I)
_DIRECT_PREFIX = re.compile(r"^(?:(?:в|с|улучшенная|улучшенной|качественный|качественным|качественная|новая|новой)\s*[,—:-]?\s*)*$", re.I)
_CONDITION_LABEL = re.compile(r"\b(?:состояние(?:\s+квартиры)?|ремонт|отделка)\s*[:—-]?\s*$", re.I)
_DECORATION = " \t•*✔✅☑️💥–—-:;,.()[]"


def _excerpt(unit: str, start: int, end: int) -> str:
    if len(unit) <= 500:
        return unit.strip()
    left = max(0, min(start - 100, len(unit) - 500))
    right = min(len(unit), max(end, left + 500))
    return unit[left:right][:500].strip()


def _claim_context(unit: str, match: re.Match, condition: str, paragraph_apartment: bool) -> bool:
    before, after = unit[:match.start()], unit[match.end():]
    if "?" in unit or _HYPOTHETICAL.search(unit):
        return False
    if condition != "не новый, но аккуратный ремонт" and _NEGATED_BEFORE.search(before[-50:]):
        return False
    if _NEGATED_AFTER.search(after):
        return False
    if condition != "требуется ремонт" and _NEEDED_BEFORE.search(before):
        return False

    # A current-apartment sentence can also mention a clean entrance after a
    # comma.  Only its own clause may govern this repair claim.
    left_clause = re.split(r"[,;]", before)[-1]
    right_clause = re.split(r"[,;]", after)[0]
    if _NON_APARTMENT.search(left_clause) or _NON_APARTMENT.search(right_clause):
        return False
    direct_apartment = bool(_APARTMENT.search(unit))
    apartment = direct_apartment or paragraph_apartment
    if _GENERIC_COMPLEX.search(unit) and not direct_apartment:
        return False
    if condition == "хорошее состояние":
        return direct_apartment
    prefix = before.strip(_DECORATION)
    return apartment or bool(_CONDITION_LABEL.search(before)) or bool(_DIRECT_PREFIX.fullmatch(prefix))


def parse_repair_description(text: str | None) -> tuple[str, str] | None:
    """Return a conservative seller claim and its verbatim evidence, or None.

    Description prose is the only input; this helper never requests a website.
    Conflicting claims remain unknown rather than being resolved by a score.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    claims: dict[str, str] = {}
    complex_section = False
    for line in text[:20_000].splitlines():
        heading = line.strip().rstrip(":").casefold()
        if heading in {"описание жк", "о жилом комплексе", "о комплексе"}:
            complex_section = True
            continue
        if heading in {"описание квартиры", "о квартире", "состояние квартиры"}:
            complex_section = False
            continue
        if complex_section:
            continue
        # Separating dash clauses keeps a real 'предчистовая отделка' claim
        # distinct from the following 'можно сделать ремонт по своему вкусу'.
        for unit in re.split(r"(?<=[.!?;])\s+|\s+[—–]\s+", line):
            matches = sorted(((condition, match) for condition, pattern in _COMPILED
                              for match in pattern.finditer(unit)),
                             key=lambda pair: pair[1].end() - pair[1].start(), reverse=True)
            accepted_spans = []
            for condition, match in matches:
                if any(match.start() < end and match.end() > start for start, end in accepted_spans):
                    continue
                if _claim_context(unit, match, condition, bool(_APARTMENT.search(line))):
                    claims.setdefault(condition, _excerpt(unit, match.start(), match.end()))
                    accepted_spans.append((match.start(), match.end()))
    if not claims:
        return None
    keys = set(claims)
    if len(keys) == 1:
        condition = next(iter(keys))
        return condition, claims[condition]
    for precise in ("предчистовая отделка", "черновая отделка"):
        if precise in keys and keys <= {precise, "без отделки", "без ремонта"}:
            return precise, claims[precise]
    if keys <= {"без отделки", "без ремонта", "требуется ремонт"}:
        condition = "требуется ремонт" if "требуется ремонт" in keys else "без ремонта"
        return condition, claims[condition]
    return None
