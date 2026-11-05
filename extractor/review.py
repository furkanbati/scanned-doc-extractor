"""Flag the fields a person should check before the data is used."""

import re

from extractor.schema import DocumentType, Fields

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AMOUNT = re.compile(r"^-?\d+\.\d{2}$")


def review_notes(doctype: DocumentType, fields: Fields) -> dict[str, str]:
    """Field name -> why it needs a look. Empty when nothing looks wrong.

    Only checks what can be checked without the label: a missing value, a date
    that could not be read as a date, a total that is not a number. A wrong but
    well-formed value (a misread company name) is not caught here.
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

    return notes
