"""Interpret only explicit mortgage statements, not calculator advertisements."""

import re
from html.parser import HTMLParser
from typing import Any

_NEGATIVE = re.compile(
    r"\bипотек(?:а|и|у|ой|е)?\s*[:—–-]?\s*(?:нет|жоқ|жок)\b"
    r"(?!\s+(?:емес|проблем|ограничений|трудностей)\b)"
    r"|\bипотека\s+не\s+(?:рассматривается|проходит|подходит|предусмотрена)\b"
    r"|\bне\s+прода[её]тся\s+в\s+ипотеку\b",
    re.I,
)
_POSITIVE = re.compile(
    r"\bипотека\s*[:—–-]\s*да\b|\b(?:можно|возможна\s+покупка)\s+в\s+ипотеку\b"
    r"|\bипотека\s+(?:возможна|доступна)\b|\bподходит\s+под\s+ипотеку\b",
    re.I,
)


def parse_mortgage_text(text: str) -> bool | None:
    text = re.sub(r"\s+", " ", text or "").strip()
    if _NEGATIVE.search(text):
        return False
    if _POSITIVE.search(text):
        return True
    return None


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
