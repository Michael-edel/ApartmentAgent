"""Interpret only explicit mortgage statements, not calculator advertisements."""

import re
from html.parser import HTMLParser
from typing import Any

_NEGATIVE = re.compile(
    r"\bипотек(?:а|и|у|ой|е)?\s*[:—–-]?\s*(?:нет|жоқ|жок)\b"
    r"(?!\s+(?:емес|проблем|ограничений|трудностей|информации|данных|ответа|решения|вопросов)\b)"
    r"|\bипотек(?:а|у)\s+(?:не\s+(?:рассматрива(?:ется|ем|ют)|рассмотрим|проходит|подходит|предусмотрена)|недоступна|невозможна)\b"
    r"|\bне\s+(?:прода[её]тся\s+в\s+ипотеку|рассматрива(?:ем|ют)\s+ипотеку|рассмотрим\s+ипотеку|подходит\s+под\s+ипотеку)\b",
    re.I,
)
_POSITIVE = re.compile(
    r"\bипотека(?:\s*[:—–-]\s*|\s+)да\b|\b(?:можно|возможна\s+покупка)\s+(?:в|под)\s+ипотеку\b"
    r"|\bипотека\s+(?:возможна|доступна|рассматривается)\b"
    r"|\b(?:возможна|доступна)\s+(?:партн[её]рская\s+)?ипотека\b"
    r"|\bподходит\s+под\s+ипотеку\b"
    r"|\bипотеку\s+(?:рассмотрим|рассматриваем|рассматривают)\b"
    r"|\b(?:рассмотрим|рассматриваем)\s+(?:любую\s+)?ипотеку\b"
    r"|\b(?:покупка|продажа)\s+в\s+ипотеку\b"
    r"|\b(?:возможно|доступно)\s+оформление\s+(?:(?:любого\s+вида|партн[её]рской|банковской)\s+){0,3}ипотеки\b",
    re.I,
)
_PAYMENT_OPTION = re.compile(r"^[\s•*✔✅☑️—–-]*ипотека\s*[,;:—–-]\s*документы\s+(?:есть|готовы|в\s+порядке)\s*[.!]?\s*$", re.I)
_CASH_ONLY = re.compile(r"\bтолько\s+(?:(?:за|с|по)\s+)?наличн(?:ый|ые|ыми|ых|ым|ого)(?:\s+расч[её]т(?:ом|а)?)?\b", re.I)
_SERVICE = re.compile(r"\b(?:консультаци\w*|калькулятор\w*|рассчит(?:ать|аем|айте)|сопровождени\w*|услуги\s+(?:по\s+)?(?:оформлени\w*|ипотек\w*))\b", re.I)
_UNCERTAIN = re.compile(r"\b(?:не\s+уверен[аы]?|под\s+вопросом|нужно\s+уточнить|пока\s+не\s+зна\w*|в\s+будущем|после\s+(?:получения|оформления|сдачи))\b", re.I)
_NEGATED_PREFIX = re.compile(r"\b(?:не\s+(?:(?:всегда|только)\s+)?|невозможна\s+|запрещена\s+)$", re.I)
_CASH_PROMOTION = re.compile(r"\b(?:скидк\w*|акци\w*|торг\w*|бонус\w*)\b", re.I)
_OTHER_PAYMENT = re.compile(r"\b(?:первоначальн\w*\s+взнос\w*|перв(?:ый|ого)\s+взнос\w*|аванс\w*|задаток\w*|комисси\w*|услуг\w*)\b", re.I)
_SALE_CONTEXT = re.compile(r"\b(?:квартира|жиль[еёя]|апартаменты|прода[её]тся|продажа|реализуется)\b", re.I)


def _unit(text: str, start: int, end: int) -> tuple[str, int, int]:
    separators = ".!?;\r\n"
    left = max((text.rfind(char, 0, start) for char in separators), default=-1) + 1
    next_stops = [position + 1 for char in separators if (position := text.find(char, end)) >= 0]
    right = min(next_stops, default=len(text))
    return text[left:right], start - left, end - left


def _evidence(unit: str, start: int, end: int) -> str:
    if len(unit) <= 500:
        return unit.strip()
    left = max(0, min(start - 120, len(unit) - 500))
    return unit[left:left + 500].strip()


def _allowed(unit: str, start: int, *, cash: bool = False) -> bool:
    if "?" in unit or _SERVICE.search(unit) or _UNCERTAIN.search(unit):
        return False
    if _NEGATED_PREFIX.search(unit[:start][-80:]):
        return False
    if cash and (_CASH_PROMOTION.search(unit) or _OTHER_PAYMENT.search(unit)):
        return False
    return True


def parse_mortgage_claim(text: str | None) -> tuple[bool, str] | None:
    """Return an explicit seller payment claim and literal evidence, not bank approval.

    Only already imported advert prose is examined. No site or mortgage service
    is requested. Cash-only and a positive option together remain ambiguous;
    an explicit mortgage refusal retains its existing priority.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    text = text[:22_000]  # Description (20k) plus the current advert's title (300).
    for match in _NEGATIVE.finditer(text):
        unit, start, end = _unit(text, match.start(), match.end())
        if _allowed(unit, start):
            return False, _evidence(unit, start, end)
    positive, cash_only = [], []
    for match in _POSITIVE.finditer(text):
        unit, start, end = _unit(text, match.start(), match.end())
        if (_allowed(unit, start)
                and ("оформление" not in match.group().casefold() or _SALE_CONTEXT.search(unit))):
            positive.append(_evidence(unit, start, end))
    for line in text.splitlines():
        if _PAYMENT_OPTION.fullmatch(line) and _allowed(line, 0):
            positive.append(line.strip())
    for match in _CASH_ONLY.finditer(text):
        unit, start, end = _unit(text, match.start(), match.end())
        if _allowed(unit, start, cash=True):
            cash_only.append(_evidence(unit, start, end))
    if positive and cash_only:
        return None
    if positive:
        return True, positive[0]
    if cash_only:
        return False, cash_only[0]
    return None


def parse_mortgage_text(text: str) -> bool | None:
    found = parse_mortgage_claim(text)
    return found[0] if found else None


def mortgage_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"нет", "жоқ", "жок", "false"}:
            return False
        if normalized in {"да", "true"}:
            return True
        return parse_mortgage_text(normalized)
    return None


class _MortgageFields(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[bool] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "img", "input", "meta", "link", "hr", "source", "wbr"}:
            return
        attributes = dict(attrs)
        selected = (
            attributes.get("data-testid") in {"mortgage", "mortgage-status", "description", "advert-description"}
            or attributes.get("data-property") == "mortgage"
            or attributes.get("itemprop") == "description"
            or "offer__description" in (attributes.get("class") or "").split()
            or "a-list__item" in (attributes.get("class") or "").split()
        )
        self.stack.append(tag not in {"script", "style"} and (selected or bool(self.stack and self.stack[-1])))

    def handle_endtag(self, tag: str) -> None:
        if tag not in {"br", "img", "input", "meta", "link", "hr", "source", "wbr"} and self.stack:
            self.stack.pop()
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1]:
            self.parts.append(data)


def mortgage_from_html(html: str) -> bool | None:
    parser = _MortgageFields()
    parser.feed(html)
    return parse_mortgage_text(" ".join(parser.parts))
