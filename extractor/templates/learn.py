"""Learning a rule set from a few documents of one format, without a model.

The user gives three (or more) documents of the same format with the values
they confirmed. For each field, every way the value could be found in each
example is listed (a label before it, the line above it, its place among the
dates or amounts, the same text on every document); the ways all examples
share are tried in order of preference, and the first that reads every
example right becomes the field's rule.

Self-check: each example in turn is left out, the rules are learned from the
others and must read it right. A field that fails needs another example: its
value does not follow one layout in the examples so far.
"""

from dataclasses import dataclass, field
from datetime import date
from itertools import combinations

from extractor.ocr.base import OcrLine
from extractor.schema import DocumentType, Fields, loose, normalize_text
from extractor.templates.read import extract, read_field
from extractor.templates.values import (
    find_values,
    normalized,
    printed_in,
    same_value,
    similarity,
    words,
)

MIN_EXAMPLES = 3
MAX_EXAMPLES = 6
# Words of a label, at most; lines a text value may span.
LABEL_WORDS = 4
TEXT_LINES = 6
# Other labels kept to check the chosen one on new documents, at most.
ALSO = 4
# A window of lines holds a text value at this similarity (0-100).
TEXT_FOUND = 80
# Texts this alike on every example are the format's constant (0-100).
CONSTANT_ALIKE = 90
# A line belongs to the fingerprint with a few letters, printed alike on
# every example; at most this many lines are kept.
FINGERPRINT_LETTERS = 4
FINGERPRINT_ALIKE = 90
FINGERPRINT_LINES = 40


@dataclass(frozen=True)
class Example:
    lines: list[OcrLine]
    values: Fields


@dataclass
class Learned:
    """A rule set, or the fields that need another example and why.

    `misfits`: field -> the example (from 0) that does not follow the others'
    layout for it; the rule learned without it reads all the others. Replacing
    that example, rather than adding one, is what helps.
    """

    template: dict | None
    unstable: dict[str, str] = field(default_factory=dict)
    misfits: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class _Spot:
    line: int
    count: int
    start: int


def learn(name: str, doctype: DocumentType, examples: list[Example]) -> Learned:
    if len(examples) < MIN_EXAMPLES:
        raise ValueError(f"a rule set needs at least {MIN_EXAMPLES} examples, got {len(examples)}")

    rules, unstable = {}, {}
    for spec in doctype.fields:
        texts = [[line.text for line in example.lines] for example in examples]
        values = [example.values.get(spec.name) for example in examples]
        if not all((value or "").strip() for value in values):
            unstable[spec.name] = "a value is missing on an example"
            continue
        rule = learn_field(spec.type, texts, values)
        if rule is None:
            unstable[spec.name] = "no rule reads it on every example"
            continue
        failed = self_check(spec.type, texts, values)
        if failed is not None:
            unstable[spec.name] = f"learned from the other examples, the rule misreads example {failed + 1}"
            continue
        rules[spec.name] = rule

    if unstable:
        return Learned(None, unstable, misfits(doctype, examples, unstable))

    template = {
        "name": name,
        "doctype": doctype.name,
        "created": date.today().isoformat(),
        "examples": len(examples),
        "fields": rules,
        "fingerprint": fingerprint([example.lines for example in examples]),
    }
    # The rule set must read its own examples right as a whole.
    for number, example in enumerate(examples, start=1):
        read = extract(template, doctype, example.lines)
        wrong = [spec.name for spec in doctype.fields
                 if not same_value(spec.type, read[spec.name], example.values[spec.name])]
        if wrong:
            return Learned(None, {name: f"misread on example {number}" for name in wrong})

    return Learned(template)


def misfits(doctype: DocumentType, examples: list[Example], fields) -> dict[str, int]:
    """For each field, the one example without which the others agree."""

    kinds = {spec.name: spec.type for spec in doctype.fields}
    found = {}
    for name in fields:
        for left_out in range(len(examples)):
            others = [example for i, example in enumerate(examples) if i != left_out]
            texts = [[line.text for line in example.lines] for example in others]
            values = [example.values.get(name) for example in others]
            if not all((value or "").strip() for value in values):
                continue
            if learn_field(kinds[name], texts, values) is not None and (
                len(others) < MIN_EXAMPLES or self_check(kinds[name], texts, values) is None
            ):
                found[name] = left_out
                break

    return found


def self_check(kind: str, texts: list[list[str]], values: list[str]) -> int | None:
    """The first example the rule learned from the others misreads, or None."""

    for left_out in range(len(texts)):
        others = [i for i in range(len(texts)) if i != left_out]
        rule = learn_field(kind, [texts[i] for i in others], [values[i] for i in others])
        if rule is None or not same_value(kind, read_field(rule, kind, texts[left_out]), values[left_out]):
            return left_out

    return None


def learn_field(kind: str, texts: list[list[str]], values: list[str]) -> dict | None:
    """The preferred rule that reads the value right on every example.

    Other labels that read every example right too are kept under "also": a
    few examples cannot tell the right label from one that only happened to
    agree ("CASH" when every example was paid exactly). On a new document they
    must still agree, or the gate sends it on (extractor/templates/read.py).
    """

    working = [rule for rule in candidates(kind, texts, values)
               if all(same_value(kind, read_field(rule, kind, lines), value) for lines, value in zip(texts, values))]
    if not working:
        return None

    rule = dict(working[0])
    if rule["kind"] == "label":
        also = [other for other in working[1:] if other["kind"] == "label"][:ALSO]
        if also:
            rule["also"] = also

    return rule


def candidates(kind: str, texts: list[list[str]], values: list[str]) -> list[dict]:
    """Rules every example allows, most preferred first: a constant, a label
    on the same line, a label above, the n-th date or amount."""

    rules = []
    if kind == "text" and all(similarity(a, b) >= CONSTANT_ALIKE for a, b in combinations(values, 2)):
        # The value the examples agree on most, should a person have typed
        # one slightly differently.
        value = max(values, key=lambda value: (sum(normalize_text(value) == normalize_text(other) for other in values),
                                               sum(similarity(value, other) for other in values)))
        rules.append({"kind": "constant", "value": value.strip(), "lines": _constant_lines(value, texts)})

    shared = None
    for lines, value in zip(texts, values):
        spots = locate(kind, lines, value)
        if not spots:
            return rules
        keys = set()
        for spot in spots:
            keys |= _keys(kind, lines, spot)
        shared = keys if shared is None else shared & keys

    labels = sorted((key for key in shared if key[0] in ("same", "below")),
                    key=lambda key: (key[0] != "same", len(key[1]), key[2] is None, key[1], key[2] or 0, key[3] or ()))
    for key in labels:
        where, label, count, stop = key
        for occurrence in ("first", "last"):
            rule = {"kind": "label", "where": where, "label": list(label), "occurrence": occurrence}
            if count:
                rule["count"] = count
            if stop:
                rule["stop"] = list(stop)
            rules.append(rule)

    rules += [{"kind": "nth", "index": key[1]} for key in sorted(key for key in shared if key[0] == "nth")]

    return rules


def _constant_lines(value: str, texts: list[list[str]]) -> int:
    """How many lines the constant takes where it is printed best."""

    best, size = 0.0, 1
    for lines in texts:
        for index in range(len(lines)):
            for count in range(1, TEXT_LINES + 1):
                score = printed_in(value, " ".join(lines[index:index + count]))
                if score > best:
                    best, size = score, count

    return size


def locate(kind: str, lines: list[str], value: str) -> list[_Spot]:
    """Where the value is printed: line, number of lines, and start in the line."""

    if kind != "text":
        wanted = normalized(kind, value)
        return [_Spot(index, 1, found.start) for index, text in enumerate(lines)
                for found in find_values(text, kind) if found.value == wanted]

    spots, best = [], TEXT_FOUND
    for index in range(len(lines)):
        for count in range(1, TEXT_LINES + 1):
            if index + count > len(lines):
                break
            score = similarity(" ".join(lines[index:index + count]), value)
            if score > best:
                spots, best = [_Spot(index, count, 0)], score
            elif score == best and spots:
                spots.append(_Spot(index, count, 0))
        # The value after a label on its line ("Address: NO 1, JALAN ...").
        start = _start_in_line(lines[index], value)
        if start:
            spots.append(_Spot(index, 1, start))

    return spots


def _start_in_line(text: str, value: str) -> int | None:
    """Where the value starts in a line that holds it after other words."""

    first = words(value)[:1]
    target = loose(value)
    if not first or not target or target not in (loose(text) or ""):
        return None
    position = text.upper().find(first[0])
    while position > 0:
        if loose(text[position:]) and similarity(text[position:], value) >= TEXT_FOUND:
            return position
        position = text.upper().find(first[0], position + 1)

    return None


def _keys(kind: str, lines: list[str], spot: _Spot) -> set[tuple]:
    keys = set()

    before = words(lines[spot.line][:spot.start]) if spot.start or kind != "text" else []
    for size in range(1, min(len(before), LABEL_WORDS) + 1):
        if kind != "text" or spot.count == 1:
            keys.add(("same", tuple(before[-size:]), None, None))

    if spot.line > 0:
        above = words(lines[spot.line - 1])
        for size in range(1, min(len(above), LABEL_WORDS) + 1):
            label = tuple(above[-size:])
            if kind != "text":
                keys.add(("below", label, None, None))
                continue
            keys.add(("below", label, spot.count, None))
            end = spot.line + spot.count
            if end < len(lines):
                stop = words(lines[end])
                for length in (1, 2):
                    if len(stop) >= length:
                        keys.add(("below", label, None, tuple(stop[:length])))

    if kind != "text":
        values = [(index, found.start) for index, text in enumerate(lines) for found in find_values(text, kind)]
        position = values.index((spot.line, spot.start))
        keys.add(("nth", position, None, None))
        keys.add(("nth", position - len(values), None, None))

    return keys


def fingerprint(examples: list[list[OcrLine]]) -> list[str]:
    """Lines printed alike on every example: the format's fixed text."""

    first, others = examples[0], examples[1:]
    fixed = []
    for line in first:
        text = line.text.strip()
        letters = sum(char.isalpha() for char in text)
        if letters < FINGERPRINT_LETTERS or any(similarity(text, kept) >= FINGERPRINT_ALIKE for kept in fixed):
            continue
        if all(any(similarity(text, other.text) >= FINGERPRINT_ALIKE for other in example) for example in others):
            fixed.append(text)
        if len(fixed) == FINGERPRINT_LINES:
            break

    return fixed
