"""Choosing the rule set for a document by its fingerprint, without a user.

Each rule set keeps the lines every example printed alike (its fingerprint).
A document is of the format whose fingerprint it prints the largest share of,
if that share reaches the one the gate asks of a document read with the rule
set; otherwise it is of no known format. On a tie the longer fingerprint wins:
more fixed lines matched is stronger evidence.
"""

from dataclasses import dataclass

from extractor.ocr.base import OcrLine
from extractor.templates.read import FINGERPRINT_SHARE, fingerprint_found


@dataclass(frozen=True)
class Match:
    file: str
    template: dict
    found: int
    fixed: int

    def to_json(self) -> dict:
        return {"rule_set": self.file, "name": self.template["name"], "found": self.found, "fixed": self.fixed}


def choose(lines: list[OcrLine], saved: dict[str, dict], share: float = FINGERPRINT_SHARE) -> Match | None:
    """The rule set (of `saved`, file -> rule set) whose format the lines are, or None."""

    best = None
    for file, template in saved.items():
        found, fixed = fingerprint_found(template, lines)
        if not fixed or found < share * fixed:
            continue
        if best is None or (found / fixed, fixed) > (best.found / best.fixed, best.fixed):
            best = Match(file, template, found, fixed)

    return best
