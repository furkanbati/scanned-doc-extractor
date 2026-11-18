"""The review gate: can a result be used without anyone looking at it?

A cheap engine (E2: RapidOCR + rules) reads every document and checks its own
result. The gate accepts the result only when every check passes; anything
else goes on to a model or a person. So the gate decides how many documents
need the expensive step, and how many wrong results get through unseen.

The gate works on the API's JSON (fields' review notes, line items as
`LineItemResult.to_json()`), so the same function serves the API and the
CORD evaluation (evaluation/cord_gate.py).

Checks, all on general properties of a receipt, none on a store or layout:

- every field's review note (missing, not a date, not an amount, a total that
  differs from the line items' total: extractor/review.py);
- every line-item reason (`needs_review`: the items, subtotal, tax and total do
  not add up, no TOTAL line, an amount computed rather than read, ...);
- at least one item;
- no item after the first whose name starts like an add-on ("Extra Pearl",
  "ADD CHEESE", "+ Ice", "Tambah ..."). Receipts print toppings as lines of
  their own, priced or not, and the sums add up either way, so the arithmetic
  cannot tell an add-on from an item; on CORD these were most of the wrong
  results that passed every other check;
- strict only: no item that costs under a quarter of the item right above it,
  the same suspicion without a keyword. It catches more add-ons, at the price
  of sending many right receipts to review.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# General words for "on top of the item above" (English and Indonesian), or a
# leading "+", at the start of the name.
ADD_ON = re.compile(r"^[^0-9A-Za-z+]*(?:\+|(?:add|extra|tambah|topping)\b)", re.IGNORECASE)
CHEAP_SHARE = Decimal("0.25")
LINE_ITEMS = "line items: "


@dataclass(frozen=True)
class GateDecision:
    accepted: bool
    reasons: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"accepted": self.accepted, "reasons": list(self.reasons)}


def _amount(value: str | None) -> Decimal | None:
    try:
        return None if value is None else Decimal(value)
    except InvalidOperation:
        return None


def add_on_items(items: list[dict]) -> list[str]:
    """Names of the items after the first that start like an add-on."""

    return [item["name"] for item in items[1:] if ADD_ON.match(item["name"] or "")]


def cheap_items(items: list[dict], share: Decimal = CHEAP_SHARE) -> list[str]:
    """Names of the items that cost less than `share` of the item above."""

    names = []
    for above, item in zip(items, items[1:]):
        before, amount = _amount(above["amount"]), _amount(item["amount"])
        if before and amount and before > 0 and 0 < amount < share * before:
            names.append(item["name"])

    return names


def decide(line_items: dict | None, review: dict[str, str] | None = None, strict: bool = False) -> GateDecision:
    """Accept, or the reasons the document needs a model or a person.

    `line_items` is `LineItemResult.to_json()` (None when the engine reads
    none), `review` the fields' review notes. Reasons read like the export's
    check column: "total not found", "line items: no total".
    """

    reasons = [f"{name} {note}" for name, note in (review or {}).items()]

    if line_items is None:
        reasons.append(LINE_ITEMS + "not read")
        return GateDecision(False, reasons)

    items = line_items["items"]
    found = list(line_items["reasons"])
    if line_items["needs_review"] and not found:
        found.append("check")
    if not items and "no items" not in found:
        found.append("no items")
    found += [f"{name!r} may be an add-on to the item above" for name in add_on_items(items)]
    if strict:
        found += [f"{name!r} costs under a quarter of the item above" for name in cheap_items(items)]

    reasons += [LINE_ITEMS + reason for reason in found]

    return GateDecision(not reasons, reasons)
