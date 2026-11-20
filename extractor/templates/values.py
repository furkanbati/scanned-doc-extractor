"""Finding a field's value in OCR text, and telling two values apart.

Shared by learning (extractor/templates/learn.py) and reading
(extractor/templates/read.py), so a rule set reads values exactly the way it
learned them.
"""

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from extractor.fields.rules import MONEY
from extractor.schema import (
    COMPACT_DATE,
    DAY_MONTH_YEAR,
    MONTH_DAY_YEAR,
    NUMERIC_DATE,
    loose,
    normalize_amount,
    normalize_date,
)

# Words as a rule set sees them: runs of two or more letters, upper case.
# Digits and punctuation vary between documents of one format; words do not.
WORD = re.compile(r"[A-Z]{2,}")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATES = (NUMERIC_DATE, DAY_MONTH_YEAR, MONTH_DAY_YEAR, COMPACT_DATE)

# Letters and digits a text value may differ by from the one a person typed
# (OCR misreads) and still count as the same value: rapidfuzz ratio, 0-100.
SAME_TEXT = 85


@dataclass(frozen=True)
class Found:
    """A value in a line of text: where it is, as printed, and normalized."""

    start: int
    end: int
    raw: str
    value: str


def words(text: str) -> list[str]:
    return WORD.findall(text.upper())


def word_spans(text: str) -> list[tuple[int, int, str]]:
    return [(match.start(), match.end(), match.group()) for match in WORD.finditer(text.upper())]


def find_values(text: str, kind: str) -> list[Found]:
    """Every date or amount in the text, left to right; [] for text fields."""

    upper = text.upper()
    found: list[Found] = []

    if kind == "date":
        for pattern in DATES:
            for match in pattern.finditer(upper):
                value = normalize_date(match.group()) or ""
                if ISO_DATE.match(value) and not any(
                    match.start() < other.end and other.start < match.end() for other in found
                ):
                    found.append(Found(match.start(), match.end(), match.group(), value))
    elif kind == "amount":
        for match in MONEY.finditer(upper):
            whole, cents = match.groups()
            found.append(Found(match.start(), match.end(), match.group(), f"{int(whole.replace(',', ''))}.{cents}"))

    return sorted(found, key=lambda value: value.start)


def normalized(kind: str, value: str | None) -> str | None:
    if value is None:
        return None
    if kind == "date":
        return normalize_date(value)
    if kind == "amount":
        return normalize_amount(value)

    return loose(value)


def similarity(first: str | None, second: str | None) -> float:
    """How alike two texts are, ignoring case, spacing and punctuation: 0-100."""

    first, second = loose(first), loose(second)
    if not first or not second:
        return 0.0

    return fuzz.ratio(first, second)


def printed_in(value: str | None, text: str | None) -> float:
    """How well the text prints the value, maybe with more around it
    ("99 SPEED MART S/B (519537-X)" prints "99 SPEED MART S/B"): 0-100.

    The text may be at most twice as long as the value, so a short value is
    not found inside a whole page.
    """

    value, text = loose(value), loose(text)
    if not value or not text or len(text) < 0.8 * len(value) or len(text) > 2 * len(value):
        return 0.0
    if len(text) <= len(value):
        return fuzz.ratio(value, text)

    return fuzz.partial_ratio(value, text)


def same_value(kind: str, read: str | None, expected: str | None) -> bool:
    """Whether a value read from the document is the expected one.

    Dates and amounts must be equal once normalized; a text may differ by a
    few misread characters.
    """

    if read is None or expected is None:
        return False
    if kind == "text":
        return similarity(read, expected) >= SAME_TEXT

    return normalized(kind, read) == normalized(kind, expected)


def ends_with(found: list[str], label: list[str]) -> bool:
    """Whether the words end with the label, one misread letter allowed per
    word of five letters or more ("TOTAI" for "TOTAL")."""

    if not label or len(found) < len(label):
        return False

    return all(_same_word(word, wanted) for word, wanted in zip(found[-len(label):], label))


def starts_with(found: list[str], label: list[str]) -> bool:
    if not label or len(found) < len(label):
        return False

    return all(_same_word(word, wanted) for word, wanted in zip(found, label))


def _same_word(word: str, wanted: str) -> bool:
    if word == wanted:
        return True
    if len(wanted) < 5 or abs(len(word) - len(wanted)) > 1:
        return False

    return fuzz.ratio(word, wanted) >= 80
