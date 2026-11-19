"""Line items read by a model: the prompt, the JSON schema and the parsing.

The model returns the same things the line-item rules find (items, subtotal,
tax, service, discount, total), as printed. Its answer goes through the same
amount parser and the same reconciliation as the rules' result, so
`needs_review` means the same thing whichever engine read the receipt: the
numbers the model returned do not add up.
"""

import json
from decimal import Decimal

from extractor.amounts import parse_amount
from extractor.fields.line_items import LineItem, LineItemResult, reconcile

INSTRUCTIONS = """

Also return "line_items", the purchased items and the amounts at the bottom:
- items: every purchased item in printed order, each with
  - name: as printed,
  - quantity: as printed, or null,
  - amount: the line amount printed for the item (usually at the right), or null,
  - add_ons: the total of the extra lines printed under the item and belonging
    to it (toppings, "Extra ...", "+ ..."), or null. An extra line is not an item.
- subtotal, tax, service, discount, total: as printed, or null. total is the
  final amount to pay, not the cash given or the change.
Copy every amount exactly as printed. Do not compute amounts that are not printed."""

AMOUNT = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "quantity": AMOUNT, "amount": AMOUNT, "add_ons": AMOUNT},
                "required": ["name", "quantity", "amount", "add_ons"],
                "additionalProperties": False,
            },
        },
        **{name: AMOUNT for name in ("subtotal", "tax", "service", "discount", "total")},
    },
    "required": ["items", "subtotal", "tax", "service", "discount", "total"],
    "additionalProperties": False,
}


def with_line_items(schema: dict) -> dict:
    """A fields schema with a required "line_items" object added."""

    return {
        **schema,
        "properties": {**schema["properties"], "line_items": SCHEMA},
        "required": [*schema["required"], "line_items"],
    }


def _amount(value) -> Decimal | None:
    return None if value is None else parse_amount(str(value))


def parse_line_items(content: str) -> LineItemResult:
    """The "line_items" of the model's JSON answer, reconciled like the rules'.

    An answer without readable line items is a result that needs review.
    """

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return LineItemResult(needs_review=True, reasons=["the model's answer is not JSON"])

    data = data.get("line_items") if isinstance(data, dict) else None
    if not isinstance(data, dict):
        return LineItemResult(needs_review=True, reasons=["the model returned no line items"])

    items, unread = [], 0
    for entry in data.get("items") or []:
        if not isinstance(entry, dict):
            continue
        amount = _amount(entry.get("amount"))
        if amount is None:
            unread += 1
            continue
        items.append(
            LineItem(
                name=str(entry.get("name") or "").strip(),
                amount=amount,
                quantity=_amount(entry.get("quantity")),
                modifiers=_amount(entry.get("add_ons")) or Decimal(0),
            )
        )

    result = LineItemResult(
        items=items,
        subtotal=_amount(data.get("subtotal")),
        tax=_amount(data.get("tax")),
        service=_amount(data.get("service")),
        discount=_amount(data.get("discount")),
        total=_amount(data.get("total")),
    )
    reconcile(result)
    if any(item.modifiers for item in items):
        # reconcile says "folded into the item above"; here the model added them.
        result.reasons = [r for r in result.reasons if not r.startswith("modifier lines")]
        result.reasons.append("add-on lines were added to their item")
    if unread:
        result.reasons.append(f"{unread} item(s) without an amount")
    result.needs_review = bool(result.reasons)

    return result
