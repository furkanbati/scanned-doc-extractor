"""Turn extraction results into table rows and downloadable files.

Kept apart from the Streamlit page so it can be tested without a browser.
"""

import csv
import io
import json
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

REVIEW_FILL = PatternFill("solid", fgColor="FFF4CC")


def result_rows(results: list[dict]) -> list[dict]:
    """One row per document: file name, each field, what to check, time, cost.

    `results` items are {"file": name, "result": API response or None,
    "error": message or None}.
    """

    rows = []

    for item in results:
        result = item.get("result")
        if result is None:
            rows.append({"file": item["file"], "check": f"failed: {item.get('error')}"})
            continue

        row = {"file": item["file"], **result["fields"]}
        gate = result.get("gate")
        if gate is not None:
            # Everything the gate found: field notes, line-item reasons, add-ons.
            notes = list(gate["reasons"])
        else:
            notes = [f"{field} {note}" for field, note in result.get("review", {}).items()]
            line_items = result.get("line_items")
            if line_items and line_items["needs_review"]:
                notes.append(line_items_note(line_items))
        if result.get("pages", 1) > 1:
            notes.append(f"only page 1 of {result['pages']} read")
        if result.get("warning"):
            # The rule set did not read it: another engine did, or no one could.
            notes.insert(0, "rule set did not fit")
        row["check"] = "; ".join(notes)
        if "chain" in result:
            # The engine whose result this is; in a chained mode, the first one
            # whose checks all passed, or the last one tried.
            row["engine"] = engine_name(result)
        row["seconds"] = result["seconds"]
        row["usd"] = result["usd"]
        rows.append(row)

    return rows


def line_items_note(line_items: dict) -> str:
    return "line items: " + ", ".join(line_items["reasons"] or ["check"])


def engine_name(result: dict) -> str:
    """Who read the document, and from what: "e2 (PDF text)", "e-Fatura XML"."""

    if result["method"] == "efatura_xml":
        return "e-Fatura XML"
    name = f"rule set: {result['template']}" if result.get("template") else result["method"]

    return name + " (PDF text)" if result.get("text_source") == "pdf" else name


def _items_check(result: dict) -> str:
    """Why a receipt's items need a look, or "" when they do not."""

    gate = result.get("gate")
    # A rule set's gate checks the fields only; the items keep their own reasons.
    if gate is not None and not result.get("template"):
        return "; ".join(reason for reason in gate["reasons"] if reason.startswith("line items: "))

    line_items = result["line_items"]

    return line_items_note(line_items) if line_items["needs_review"] else ""


def _number(value: str | None) -> int | float | None:
    """An amount from the API ("43636", "9999.99") as a number a spreadsheet can sum."""

    if value is None:
        return None

    amount = Decimal(value)

    return int(amount) if amount == amount.to_integral_value() else float(amount)


def line_item_rows(results: list[dict]) -> list[dict]:
    """One row per purchased item: file, its position, name, quantity, unit
    price, amount. Items of a receipt whose numbers do not add up carry the
    reason in `check`."""

    rows = []

    for item in results:
        line_items = (item.get("result") or {}).get("line_items")
        if not line_items:
            continue

        check = _items_check(item["result"])
        for number, entry in enumerate(line_items["items"], start=1):
            rows.append(
                {
                    "file": item["file"],
                    "item": number,
                    "name": entry["name"],
                    "quantity": _number(entry["quantity"]),
                    "unit_price": _number(entry["unit_price"]),
                    "amount": _number(entry["amount"]),
                    "check": check,
                }
            )

    return rows


def columns(rows: list[dict]) -> list[str]:
    """All column names, in first-seen order (a failed row has no fields)."""

    names = []
    for row in rows:
        for name in row:
            if name not in names:
                names.append(name)

    # Keep the bookkeeping columns at the end.
    tail = [name for name in ("check", "seconds", "usd") if name in names]

    return [name for name in names if name not in tail] + tail


def to_csv(rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns(rows), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

    # The byte-order mark makes Excel open the file as UTF-8.
    return buffer.getvalue().encode("utf-8-sig")


def to_json(rows: list[dict], item_rows: list[dict] | None = None) -> bytes:
    """The document rows; with item rows, each document lists its items under "line_items"."""

    if item_rows:
        rows = [
            {
                **row,
                "line_items": [
                    {name: value for name, value in entry.items() if name not in ("file", "check")}
                    for entry in item_rows
                    if entry["file"] == row["file"]
                ],
            }
            for row in rows
        ]

    return json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")


def to_xlsx(rows: list[dict], item_rows: list[dict] | None = None) -> bytes:
    """A "Documents" sheet, and a "Line items" sheet when there are items.
    Rows to check are highlighted on both."""

    workbook = Workbook()
    _fill_sheet(workbook.active, "Documents", rows)
    if item_rows:
        _fill_sheet(workbook.create_sheet(), "Line items", item_rows)

    buffer = io.BytesIO()
    workbook.save(buffer)

    return buffer.getvalue()


def _fill_sheet(sheet, title: str, rows: list[dict]) -> None:
    names = columns(rows)
    sheet.title = title

    sheet.append(names)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for row in rows:
        sheet.append([row.get(name) for name in names])
        if row.get("check"):
            for cell in sheet[sheet.max_row]:
                cell.fill = REVIEW_FILL

    for index, name in enumerate(names, start=1):
        longest = max([len(name)] + [len(str(row.get(name) or "")) for row in rows])
        sheet.column_dimensions[sheet.cell(1, index).column_letter].width = min(60, longest + 2)
    sheet.freeze_panes = "A2"
