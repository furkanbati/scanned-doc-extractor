"""Reading a document with a user's rule set, and deciding whether the
result can be used without a look.

A rule set (extractor/templates/learn.py) holds one rule per field and a
fingerprint, the lines every example printed alike. Rule kinds:

- constant: the field is the same on every document of the format (the
  seller's name, its address); read as the value the user confirmed, when the
  document prints it;
- label: the value follows a fixed label, on the same line ("TOTAL 9.00") or
  on the lines below it ("Bill to" and the address under it);
- nth: the n-th date or amount of the document, counted from the top (or
  from the bottom when negative), when no label marks it.
"""

from rapidfuzz import fuzz, process

from extractor.gate import GateDecision
from extractor.ocr.base import OcrLine
from extractor.schema import DocumentType, Fields, loose
from extractor.templates.values import (
    ends_with,
    find_values,
    printed_in,
    same_value,
    starts_with,
    word_spans,
    words,
)

# A constant must be printed at least this alike to count as found (0-100).
CONSTANT_PRINTED = 80
# A fingerprint line counts as found at this similarity (0-100).
FINGERPRINT_LINE = 85
# Share of the fingerprint lines a document must print to be of the format.
FINGERPRINT_SHARE = 0.6
# Lines a value is looked for below its label, and lines a stop is awaited.
BELOW = 2
MAX_LINES = 8


def read_field(rule: dict, kind: str, texts: list[str]) -> str | None:
    """The field's value as printed in the document, or None if not found."""

    return read_field_at(rule, kind, texts)[0]


def read_field_at(rule: dict, kind: str, texts: list[str]) -> tuple[str | None, list[int]]:
    """The field's value and the lines it was read from ([] when not found)."""

    if rule["kind"] == "constant":
        return _constant(rule, texts)
    if rule["kind"] == "nth":
        values = [(index, value) for index, text in enumerate(texts) for value in find_values(text, kind)]
        position = rule["index"]
        if -len(values) <= position < len(values):
            index, value = values[position]
            return value.raw, [index]
        return None, []

    order = range(len(texts)) if rule["occurrence"] == "first" else range(len(texts) - 1, -1, -1)
    for index in order:
        value, lines = (_same_line if rule["where"] == "same" else _below)(rule, kind, texts, index)
        if value:
            return value, lines

    return None, []


def _constant(rule: dict, texts: list[str]) -> tuple[str | None, list[int]]:
    size = rule.get("lines", 1)
    for index in range(len(texts)):
        for count in sorted({max(1, size - 1), size, size + 1}):
            if printed_in(rule["value"], " ".join(texts[index:index + count])) >= CONSTANT_PRINTED:
                return rule["value"], list(range(index, min(index + count, len(texts))))

    return None, []


def _same_line(rule: dict, kind: str, texts: list[str], index: int) -> tuple[str | None, list[int]]:
    text, label = texts[index], rule["label"]
    if kind != "text":
        for found in find_values(text, kind):
            if ends_with(words(text[:found.start]), label):
                return found.raw, [index]
        return None, []

    spans = word_spans(text)
    for start in range(len(spans) - len(label) + 1):
        if starts_with([word for _, _, word in spans[start:]], label):
            value = text[spans[start + len(label) - 1][1]:].strip(" :;-.\t")
            return (value, [index]) if value else (None, [])

    return None, []


def _below(rule: dict, kind: str, texts: list[str], index: int) -> tuple[str | None, list[int]]:
    if not ends_with(words(texts[index]), rule["label"]):
        return None, []
    first = index + 1
    after = texts[first:]

    if kind != "text":
        for offset, text in enumerate(after[:BELOW]):
            found = find_values(text, kind)
            if found:
                return found[0].raw, [first + offset]
        return None, []

    if rule.get("count"):
        taken = after[:rule["count"]]
    else:
        taken = []
        for text in after[:MAX_LINES]:
            if starts_with(words(text), rule["stop"]):
                break
            taken.append(text)
        else:
            return None, []  # the stop never came: not this format's layout

    value = " ".join(taken).strip()
    return (value, list(range(first, first + len(taken)))) if value else (None, [])


def extract(template: dict, doctype: DocumentType, lines: list[OcrLine]) -> Fields:
    texts = [line.text for line in lines]
    kinds = {spec.name: spec.type for spec in doctype.fields}

    return {name: read_field(rule, kinds[name], texts) for name, rule in template["fields"].items()}


def disagreements(template: dict, doctype: DocumentType, lines: list[OcrLine]) -> list[str]:
    """Fields whose other learned labels read a different value than the rule."""

    texts = [line.text for line in lines]
    kinds = {spec.name: spec.type for spec in doctype.fields}
    found = []
    for name, rule in template["fields"].items():
        value, others = read_field(rule, kinds[name], texts), rule.get("also", [])
        if value is None or not others:
            continue
        reads = [read for other in others if (read := read_field(other, kinds[name], texts)) is not None]
        differ = [read for read in reads if not same_value(kinds[name], value, read)]
        if differ:
            found.append(f"{name} read as {value!r} and as {differ[0]!r} by another label")
        elif not reads:
            # The short label found a value where none of the longer ones is
            # printed: maybe not the place it was learned from.
            found.append(f"{name} found by one label only")

    return found


def fingerprint_found(template: dict, lines: list[OcrLine]) -> tuple[int, int]:
    """How many of the rule set's fixed lines the document prints, of how many."""

    printed = [loose(line.text) or "" for line in lines]
    fixed = template["fingerprint"]
    found = sum(
        process.extractOne(loose(text), printed, scorer=fuzz.ratio, score_cutoff=FINGERPRINT_LINE) is not None
        for text in fixed
    )

    return found, len(fixed)


def decide(template: dict, doctype: DocumentType, lines: list[OcrLine], review: dict[str, str],
           share: float = FINGERPRINT_SHARE) -> GateDecision:
    """Accept the rule set's result, or say why another engine should read it.

    The document must print the format's fixed lines, every field must be
    found and well formed (extractor/review.py), and the labels learned for a
    field must agree on its value.
    """

    reasons = [f"{name} {note}" for name, note in review.items()]
    reasons += disagreements(template, doctype, lines)
    found, fixed = fingerprint_found(template, lines)
    if found < share * fixed:
        reasons.insert(0, f"rule set: only {found} of its {fixed} fixed lines found, another format?")

    return GateDecision(not reasons, reasons)
