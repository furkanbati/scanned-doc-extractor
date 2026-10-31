"""Prompt and JSON schema built from a document type, shared by model extractors."""

import json

from extractor.schema import DocumentType, Fields

INSTRUCTIONS = """You extract fields from a scanned {doctype}: {description}

Return one JSON object with exactly these fields:
{fields}

Rules:
- Copy each value exactly as it is printed: same words, spelling, punctuation and
  number format. Do not translate, expand abbreviations or reformat dates.
- A value printed over several lines is joined into one string with single spaces.
- Use null when the field is not on the document."""


def instructions(doctype: DocumentType) -> str:
    return INSTRUCTIONS.format(
        doctype=doctype.name,
        description=doctype.description,
        fields="\n".join(
            f"- {field.name} ({field.type}): {field.description}"
            for field in doctype.fields
        ),
    )


def json_schema(doctype: DocumentType) -> dict:
    """Strict JSON schema: every field present, a string or null."""

    return {
        "type": "object",
        "properties": {
            field.name: {"type": ["string", "null"], "description": field.description}
            for field in doctype.fields
        },
        "required": doctype.field_names,
        "additionalProperties": False,
    }


def parse_fields(content: str, doctype: DocumentType) -> Fields:
    """Read the model's JSON answer; anything unreadable counts as no fields."""

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return doctype.values({})

    if not isinstance(data, dict):
        return doctype.values({})

    return doctype.values(
        {key: (str(value) if value is not None else None) for key, value in data.items()}
    )
