"""Rule sets learned without a user, from a teacher's answers.

A document of no known format is read by a model (the teacher: E5, or E3).
When the teacher's answer passes its checks (teacher_checks: every field the
type requires found and well formed, and, where the type has a subtotal, a
tax and a total, those adding up), the document becomes an example of its
format:
documents printing enough of the same fixed lines are put in one group. Once
a group has three examples, the rule set is learned from them as from a
user's (extractor/templates/learn.py) and saved, marked `auto`.

Safeguards, since no person looks:

- only answers that pass the teacher's checks become examples (the line
  items are not asked to add up: on SROIE dev the gate accepted 27 of E5's
  226 answers, and E5 reads no items on invoices; reports/auto_rule_sets.md);
- learning's own self-check, and the rule set's gate on every document;
- probation: the first documents a new rule set accepts are read by the
  teacher too; one disagreement and the rule set is removed;
- audit: a small share of what it accepts afterwards is marked for a person.

Groups waiting for examples are kept as JSON in the rule sets' folder,
under pending/.
"""

import json
import random
from decimal import Decimal, InvalidOperation
import threading
import uuid
from datetime import date
from pathlib import Path

from extractor.ocr.base import OcrLine
from extractor.review import review_notes
from extractor.schema import DocumentType
from extractor.templates import store
from extractor.templates.learn import (
    FINGERPRINT_LETTERS,
    MAX_EXAMPLES,
    MIN_EXAMPLES,
    Example,
    fingerprint,
    learn,
)
from extractor.templates.values import same_value

# Two documents are of one format when this share of the shorter one's
# lettered lines is printed alike on the other. On SROIE dev, receipts of
# different stores reach at most 0.21, those of one store 0.23 and up (6 of
# 578 pairs under 0.3; reports/auto_rule_sets.md).
GROUP_ALIKE = 0.3
# Documents a new rule set's accepted reads are checked against the teacher.
PROBATION = 5
# Share of a rule set's accepted reads marked for a person, after probation.
AUDIT_SHARE = 0.02

# Subtotal plus tax may differ from the total by this much from rounding.
TOLERANCE = Decimal("0.01")
# The amount fields whose sum is checked, when a document type has all three.
SUM_FIELDS = ("subtotal", "tax", "total")

_lock = threading.Lock()


def teacher_checks(doctype: DocumentType, fields: dict) -> list[str]:
    """Why a teacher's answer cannot be an example; empty when it can."""

    reasons = [f"{name} {note}" for name, note in review_notes(doctype, fields).items()]
    kinds = {spec.name: spec.type for spec in doctype.fields}
    if all(kinds.get(name) == "amount" for name in SUM_FIELDS):
        normalized = doctype.normalize(fields)
        try:
            subtotal, tax, total = (Decimal(normalized[name]) for name in SUM_FIELDS)
        except (TypeError, InvalidOperation):
            return reasons  # one is missing: optional, or already a reason
        if abs(subtotal + tax - total) > TOLERANCE:
            reasons.append(f"subtotal {subtotal} plus tax {tax} is not the total {total}")

    return reasons


def pending_dir() -> Path:
    return store.directory() / "pending"


def groups() -> list[dict]:
    folder = pending_dir()
    if not folder.is_dir():
        return []

    return [json.loads(file.read_text(encoding="utf-8")) for file in sorted(folder.glob("*.json"))]


def _save_group(group: dict) -> None:
    pending_dir().mkdir(parents=True, exist_ok=True)
    (pending_dir() / f"{group['id']}.json").write_text(json.dumps(group, ensure_ascii=False), encoding="utf-8")


def _drop_group(group: dict) -> None:
    (pending_dir() / f"{group['id']}.json").unlink(missing_ok=True)


def forget(group_id: str) -> bool:
    """Drop a waiting group and its examples; False when there is none."""

    with _lock:
        target = pending_dir() / f"{group_id}.json"
        if not target.is_file() or target.parent != pending_dir():
            return False
        target.unlink()

    return True


def lettered(lines: list[OcrLine]) -> int:
    return sum(sum(char.isalpha() for char in line.text) >= FINGERPRINT_LETTERS for line in lines)


def format_likeness(first: list[OcrLine], second: list[OcrLine]) -> float:
    """Share of the shorter document's lettered lines printed alike on both."""

    smaller = min(lettered(first), lettered(second))
    if not smaller:
        return 0.0

    return len(fingerprint([first, second])) / smaller


def _lines(rows: list[dict]) -> list[OcrLine]:
    return [OcrLine(text=row["text"], box=tuple(row["box"])) for row in rows]


def find_group(lines: list[OcrLine], doctype: str, waiting: list[dict]) -> dict | None:
    """The waiting group of this format: the best likeness to its first
    example, at least GROUP_ALIKE."""

    best, score = None, GROUP_ALIKE
    for group in waiting:
        if group["doctype"] != doctype:
            continue
        likeness = format_likeness(lines, _lines(group["examples"][0]["lines"]))
        if likeness >= score:
            best, score = group, likeness

    return best


def observe(lines: list[OcrLine], values: dict, doctype: DocumentType, teacher: str) -> dict:
    """Keep a document the teacher read, and learn its format's rule set once
    there are enough examples. Returns what happened, for the API's answer."""

    with _lock:
        waiting = groups()
        group = find_group(lines, doctype.name, waiting)
        if group is None:
            group = {"id": uuid.uuid4().hex[:12], "doctype": doctype.name, "teacher": teacher,
                     "created": date.today().isoformat(), "examples": []}
        group["examples"].append({"lines": [{"text": line.text, "box": list(line.box)} for line in lines],
                                  "values": dict(values)})
        status = {"group": group["id"], "examples": len(group["examples"]), "needed": MIN_EXAMPLES,
                  "learned": None}
        if len(group["examples"]) < MIN_EXAMPLES:
            _save_group(group)
            return status

        examples = [Example(_lines(example["lines"]), doctype.values(example["values"]))
                    for example in group["examples"]]
        learned = learn(_name(group, doctype), doctype, examples)
        if learned.template is None:
            if learned.misfits and len(group["examples"]) >= MAX_EXAMPLES:
                # The example most fields name as unlike the others goes.
                counts = list(learned.misfits.values())
                group["examples"].pop(max(set(counts), key=counts.count))
            elif len(group["examples"]) >= MAX_EXAMPLES:
                group["examples"].pop(0)
            status["unstable"] = learned.unstable
            _save_group(group)
            return status

        template = {**learned.template, "auto": True, "teacher": teacher, "probation": PROBATION}
        store.save(template)
        _drop_group(group)
        status["learned"] = store.slug(template["name"])

        return status


def _name(group: dict, doctype: DocumentType) -> str:
    """The issuer the teacher read most, else the group's id; unique."""

    names = [example["values"].get(doctype.field_names[0]) for example in group["examples"]]
    names = [name for name in names if name and any(char.isalnum() for char in name)]
    base = f"{max(set(names), key=names.count)} (auto)" if names else f"Format {group['id']} (auto)"
    name, number = base, 2
    while store.path(name).exists():
        name, number = f"{base} {number}", number + 1

    return name


def on_probation(template: dict) -> bool:
    return bool(template.get("auto")) and template.get("probation", 0) > 0


def check(file: str, template: dict, doctype: DocumentType, read: dict, teacher: dict) -> dict:
    """A rule set on probation against the teacher on one document it
    accepted: one more agreement, or removed at the first disagreement."""

    differ = [spec.name for spec in doctype.fields
              if (teacher.get(spec.name) or "").strip() and (read.get(spec.name) or "").strip()
              and not same_value(spec.type, read[spec.name], teacher[spec.name])]
    with _lock:
        if differ:
            store.path(file).unlink(missing_ok=True)
            return {"rule_set": file, "agreed": False, "differ": differ, "removed": True}
        current = store.load(file)
        current["probation"] = max(0, current.get("probation", 0) - 1)
        store.save(current, replace=True)

    return {"rule_set": file, "agreed": True, "left": current["probation"], "removed": False}


def audit(template: dict) -> bool:
    """Whether to mark this accepted read of an automatic rule set for a person."""

    return bool(template.get("auto")) and not on_probation(template) and random.random() < AUDIT_SHARE
