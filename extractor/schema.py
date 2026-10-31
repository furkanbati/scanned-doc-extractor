"""Document types and value normalization.

A document type (extractor/doctypes/<name>.yaml) lists the fields to extract,
each with a type that decides how values are normalized before comparison.
Extracted values travel as plain dicts: {field name: value or None}.
"""

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

DOCTYPE_DIR = Path(__file__).parent / "doctypes"

Fields = dict[str, str | None]

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

NUMERIC_DATE = re.compile(r"(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{2,4})")
COMPACT_DATE = re.compile(r"\b(\d{8})\b")  # YYYYMMDD, else DDMMYYYY
DAY_MONTH_YEAR = re.compile(r"(\d{1,2})[\s\-/.]*([A-Z]{3})[A-Z]*[\s\-/.,]*(\d{2,4})")
MONTH_DAY_YEAR = re.compile(r"([A-Z]{3})[A-Z]*[\s\-/.]*(\d{1,2})[\s\-/.,]+(\d{2,4})")
AMOUNT = re.compile(r"-?\d+(?:\.\d+)?")


class FieldSpec(BaseModel):
    name: str
    type: Literal["text", "date", "amount"]
    description: str = ""


class DocumentType(BaseModel):
    """The fields to extract from one kind of document."""

    name: str
    description: str = ""
    fields: list[FieldSpec]

    @property
    def field_names(self) -> list[str]:
        return [field.name for field in self.fields]

    def values(self, data: dict) -> Fields:
        """Keep this type's fields from data, None for the missing ones."""

        return {name: data.get(name) for name in self.field_names}

    def normalize(self, values: Fields) -> Fields:
        return {
            field.name: NORMALIZERS[field.type](values.get(field.name))
            for field in self.fields
        }


def load_doctype(name: str | Path) -> DocumentType:
    """Load extractor/doctypes/<name>.yaml, or a YAML file at the given path."""

    path = Path(name)
    if not path.is_file():
        path = DOCTYPE_DIR / f"{name}.yaml"

    with path.open(encoding="utf-8") as file:
        return DocumentType(**yaml.safe_load(file))


def normalize_text(value: str | None) -> str | None:
    """Upper case, single spaces, no space before punctuation."""

    if value is None:
        return None

    text = re.sub(r"\s+", " ", value.upper()).strip()
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)

    return text or None


def _to_year(value: str) -> int:
    year = int(value)

    return year + 2000 if year < 100 else year


def _make_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def normalize_date(value: str | None) -> str | None:
    """Parse a receipt date (day first, as on Malaysian receipts) to YYYY-MM-DD.

    Month first is tried only when day first is not a valid date (12/28/2017).

    Dates that cannot be parsed fall back to normalize_text, so an exact match
    is still possible.
    """

    if value is None:
        return None

    text = normalize_text(value)
    if text is None:
        return None

    match = NUMERIC_DATE.search(text)
    if match:
        first, second, third = match.groups()

        if len(first) == 4:
            parsed = _make_date(int(first), int(second), int(third))
        else:
            year = _to_year(third)
            parsed = _make_date(year, int(second), int(first)) or _make_date(
                year, int(first), int(second)
            )

        if parsed:
            return parsed

    match = COMPACT_DATE.search(text)
    if match:
        digits = match.group(1)
        parsed = _make_date(
            int(digits[:4]), int(digits[4:6]), int(digits[6:])
        ) or _make_date(int(digits[4:]), int(digits[2:4]), int(digits[:2]))
        if parsed:
            return parsed

    for pattern, order in ((DAY_MONTH_YEAR, "dmy"), (MONTH_DAY_YEAR, "mdy")):
        match = pattern.search(text)
        if not match:
            continue

        groups = dict(zip(order, match.groups()))
        month = MONTHS.get(groups["m"])
        if month is None:
            continue

        parsed = _make_date(_to_year(groups["y"]), month, int(groups["d"]))
        if parsed:
            return parsed

    return text


def normalize_amount(value: str | None) -> str | None:
    """Parse an amount to a two-decimal string: 'RM 1,234.5' -> '1234.50'.

    Amounts that cannot be parsed fall back to normalize_text.
    """

    if value is None:
        return None

    text = normalize_text(value)
    if text is None:
        return None

    match = AMOUNT.search(text.replace(",", "").replace(" ", ""))
    if not match:
        return text

    try:
        amount = Decimal(match.group())
    except InvalidOperation:
        return text

    return f"{amount.quantize(Decimal('0.01'))}"


NORMALIZERS: dict[str, Callable[[str | None], str | None]] = {
    "text": normalize_text,
    "date": normalize_date,
    "amount": normalize_amount,
}
