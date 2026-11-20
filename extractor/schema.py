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
    # Not on every document (a discount, a second tax): when it is missing,
    # nothing needs a look.
    optional: bool = False


class DocumentType(BaseModel):
    """The fields to extract from one kind of document.

    `line_items`: the document also lists purchased items, one per line, with
    their amounts and the totals they add up to. Engines that can read them
    (extractor/engines: `line_items:`) return them next to the fields.
    """

    name: str
    description: str = ""
    fields: list[FieldSpec]
    line_items: bool = False

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


def user_doctype_dir() -> Path:
    """Document types users define, next to their rule sets (templates/doctypes)."""

    from extractor.templates.store import directory

    return directory() / "doctypes"


def load_doctype(name: str | Path) -> DocumentType:
    """Load extractor/doctypes/<name>.yaml, a user's type of that name, or a
    YAML file at the given path."""

    path = Path(name)
    if not path.is_file():
        path = DOCTYPE_DIR / f"{name}.yaml"
    if not path.is_file():
        path = user_doctype_dir() / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"No document type named {name!r}")

    with path.open(encoding="utf-8") as file:
        return DocumentType(**yaml.safe_load(file))


def doctype_names() -> dict[str, dict]:
    """Every document type: the ones that come with the project, then the users'."""

    found = {}
    for directory, builtin in ((DOCTYPE_DIR, True), (user_doctype_dir(), False)):
        for path in sorted(directory.glob("*.yaml")):
            if path.stem in found:
                continue
            doctype = load_doctype(path)
            found[path.stem] = {
                "description": doctype.description.strip(),
                "builtin": builtin,
                "fields": [field.model_dump() for field in doctype.fields],
            }

    return found


TYPE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def save_doctype(doctype: DocumentType, replace: bool = False) -> Path:
    """Save a user's document type; the project's own types cannot be replaced."""

    if not TYPE_NAME.match(doctype.name):
        raise ValueError("A type name is lower case letters, digits and _, starting with a letter")
    if not doctype.fields:
        raise ValueError("A document type needs at least one field")
    names = [field.name for field in doctype.fields]
    if len(set(names)) != len(names) or not all(TYPE_NAME.match(name) for name in names):
        raise ValueError("Field names must differ and be lower case letters, digits and _")
    if (DOCTYPE_DIR / f"{doctype.name}.yaml").is_file():
        raise FileExistsError(f"{doctype.name!r} comes with the project and cannot be replaced")
    path = user_doctype_dir() / f"{doctype.name}.yaml"
    if path.is_file() and not replace:
        raise FileExistsError(f"A document type named {doctype.name!r} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doctype.model_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8")

    return path


def normalize_text(value: str | None) -> str | None:
    """Upper case, single spaces, no space before punctuation."""

    if value is None:
        return None

    text = re.sub(r"\s+", " ", value.upper()).strip()
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)

    return text or None


def loose(value: str | None) -> str | None:
    """Letters and digits only, so spacing and punctuation do not count.

    The SROIE labels are not consistent here ("TAMPOI, 81200" next to
    "TAMPOI,81200"), which an exact match counts as an error.
    """

    if value is None:
        return None

    return re.sub(r"[^0-9A-Z]", "", value.upper()) or None


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


# A decimal comma, as in "1.250,00 TL" or "12,50": thousands may be grouped
# with dots before it. Not with spaces: "%20 208,33" is 20 percent and 208,33.
COMMA_DECIMAL = re.compile(r"\d{1,3}(?:\.\d{3})+,\d{2}(?![\d.,])|(?<![\d.,])\d+,\d{2}(?![\d.,])")


def normalize_amount(value: str | None) -> str | None:
    """Parse an amount to a two-decimal string: 'RM 1,234.5' -> '1234.50',
    '1.250,00 TL' -> '1250.00'.

    Amounts that cannot be parsed fall back to normalize_text.
    """

    if value is None:
        return None

    text = normalize_text(value)
    if text is None:
        return None

    comma = COMMA_DECIMAL.search(text)
    if comma:
        whole, cents = comma.group().replace(".", "").split(",")
        negative = text[: comma.start()].rstrip().endswith("-")
        return f"{'-' if negative else ''}{int(whole)}.{cents}"

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
