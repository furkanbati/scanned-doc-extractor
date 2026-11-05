"""Turn extraction results into table rows and downloadable files.

Kept apart from the Streamlit page so it can be tested without a browser.
"""

import csv
import io
import json

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
        notes = [f"{field} {note}" for field, note in result.get("review", {}).items()]
        if result.get("pages", 1) > 1:
            notes.append(f"only page 1 of {result['pages']} read")
        row["check"] = "; ".join(notes)
        row["seconds"] = result["seconds"]
        row["usd"] = result["usd"]
        rows.append(row)

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


def to_json(rows: list[dict]) -> bytes:
    return json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")


def to_xlsx(rows: list[dict]) -> bytes:
    names = columns(rows)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Documents"

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

    buffer = io.BytesIO()
    workbook.save(buffer)

    return buffer.getvalue()
