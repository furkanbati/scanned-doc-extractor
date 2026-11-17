"""Flag the fields a person should check before the data is used."""

import re
from decimal import Decimal

from extractor.amounts import parse_amount
from extractor.schema import DocumentType, Fields

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AMOUNT = re.compile(r"^-?\d+\.\d{2}$")


def review_notes(
    doctype: DocumentType,
    fields: Fields,
    line_items_total: Decimal | None = None,
) -> dict[str, str]:
    """Field name -> why it needs a look. Empty when nothing looks wrong.

    Only checks what can be checked without the label: a missing value, a date
    that could not be read as a date, a total that is not a number, and, when
    line items were read, a total that differs from theirs (two readings of the
    same receipt that disagree: one of them is wrong). A wrong but well-formed
    value (a misread company name) is not caught here.
    """

    values = doctype.values(fields)
    normalized = doctype.normalize(fields)
    notes = {}

    for spec in doctype.fields:
        if not (values[spec.name] or "").strip():
            notes[spec.name] = "not found"
        elif spec.type == "date" and not ISO_DATE.match(normalized[spec.name] or ""):
            notes[spec.name] = "not a recognisable date"
        elif spec.type == "amount" and not AMOUNT.match(normalized[spec.name] or ""):
            notes[spec.name] = "not an amount"

    # The document's one amount field is its total; compare it with the line
    # items' total. Both are read with parse_amount, which takes "57.900" as
    # 57900 where the field normalizer would take it as 57.90.
    amounts = [spec.name for spec in doctype.fields if spec.type == "amount"]
    if line_items_total is not None and len(amounts) == 1:
        name = amounts[0]
        value = parse_amount(values[name])
        if name not in notes and value is not None and value != line_items_total:
            notes[name] = f"differs from the line items' total {line_items_total}"

    return notes
