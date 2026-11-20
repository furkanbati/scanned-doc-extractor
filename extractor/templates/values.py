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
    COMMA_DECIMAL,
    COMPACT_DATE,
    DAY_MONTH_YEAR,
    MONTH_DAY_YEAR,
    NUMERIC_DATE,
    loose,
    normalize_amount,
    normalize_date,
)

# Words as a rule set sees them: runs of two or more letters (any alphabet:
# "TARİHİ"), upper case. Digits and punctuation vary between documents of one
# format; words do not.
WORD = re.compile(r"[^\W\d_]{2,}")
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
        # "1.250,00" first: read with a decimal point it would be 250.00.
        for match in COMMA_DECIMAL.finditer(upper):
            found.append(Found(match.start(), match.end(), match.group(), normalize_amount(match.group())))
        for match in MONEY.finditer(upper):
            if any(match.start() < other.end and other.start < match.end() for other in found):
                continue
            whole, cents = match.groups()
            found.append(Found(match.start(), match.end(), match.group(), f"{int(whole.replace(',', ''))}.{cents}"))

    return sorted(found, key=lambda value: value.start)


def value_from_lines(kind: str, lines: list[str]) -> str | None:
    """The value in lines a person picked: the first date or amount in them,
    or for text the lines joined, without a label ending in ":" before it
    ("Fatura No: A-153" -> "A-153")."""

    if not lines:
        return None
    if kind != "text":
        for line in lines:
            found = find_values(line, kind)
            if found:
                return found[0].raw
        return None

    first, rest = lines[0], lines[1:]
    if ":" in first:
        label, after = first.split(":", 1)
        if after.strip() and len(words(label)) <= 4:
            first = after
    text = " ".join([first.strip(), *(line.strip() for line in rest)]).strip()

    return text or None


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


def mostly_digits(text: str | None) -> bool:
    """An identifier such as an invoice or tax number: half or more digits."""

    letters = loose(text) or ""
    return bool(letters) and sum(char.isdigit() for char in letters) * 2 >= len(letters)


def alike(first: str | None, second: str | None, threshold: float = SAME_TEXT) -> bool:
    """Whether two texts are the same value: a few misread letters are allowed,
    but not a different digit in an identifier ("A-2024-0153", "-0157")."""

    if mostly_digits(first) or mostly_digits(second):
        return bool(loose(first)) and loose(first) == loose(second)

    return similarity(first, second) >= threshold


def same_value(kind: str, read: str | None, expected: str | None) -> bool:
    """Whether a value read from the document is the expected one.

    Dates and amounts must be equal once normalized; a text may differ by a
    few misread characters.
    """

    if read is None or expected is None:
        return False
    if kind == "text":
        return alike(read, expected)

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
