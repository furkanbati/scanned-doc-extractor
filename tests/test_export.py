import csv
import io
import json

from openpyxl import load_workbook

from extractor.review import review_notes
from extractor.schema import load_doctype
from ui.export import columns, result_rows, to_csv, to_json, to_xlsx

RECEIPT = load_doctype("receipt")


def response(fields, review=None, pages=1, usd=0.0003):
    return {
        "fields": fields,
        "review": review or {},
        "pages": pages,
        "seconds": 7.2,
        "usd": usd,
    }


RESULTS = [
    {
        "file": "a.jpg",
        "result": response({"company": "ABC SDN BHD", "date": "25/12/2018", "address": "NO 1", "total": "9.00"}),
        "error": None,
    },
    {
        "file": "b.pdf",
        "result": response(
            {"company": "XYZ ENTERPRISE", "date": None, "address": "JOHOR", "total": "12.50"},
            review={"date": "not found"},
            pages=3,
        ),
        "error": None,
    },
    {"file": "c.png", "result": None, "error": "Extraction failed: timed out"},
]


def test_rows_carry_fields_and_what_to_check():
    rows = result_rows(RESULTS)

    assert rows[0]["company"] == "ABC SDN BHD"
    assert rows[0]["check"] == ""
    assert rows[1]["check"] == "date not found; only page 1 of 3 read"
    assert rows[2] == {"file": "c.png", "check": "failed: Extraction failed: timed out"}


def test_columns_put_the_fields_first():
    assert columns(result_rows(RESULTS)) == ["file", "company", "date", "address", "total", "check", "seconds", "usd"]


def test_csv_round_trip():
    text = to_csv(result_rows(RESULTS)).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))

    assert len(rows) == 3
    assert rows[1]["total"] == "12.50"
    assert rows[2]["company"] == ""


def test_json_keeps_missing_values_as_null():
    rows = json.loads(to_json(result_rows(RESULTS)))

    assert rows[1]["date"] is None


def test_xlsx_has_a_header_and_marks_rows_to_check():
    sheet = load_workbook(io.BytesIO(to_xlsx(result_rows(RESULTS)))).active

    assert [cell.value for cell in sheet[1]][:2] == ["file", "company"]
    assert sheet["B2"].value == "ABC SDN BHD"
    assert sheet["A2"].fill.fgColor.rgb != "00FFF4CC"
    assert sheet["A3"].fill.fgColor.rgb == "00FFF4CC"


def test_review_notes():
    notes = review_notes(
        RECEIPT,
        {"company": "ABC SDN BHD", "date": "sometime in May", "address": " ", "total": "about nine"},
    )

    assert notes == {"date": "not a recognisable date", "address": "not found", "total": "not an amount"}


def test_review_notes_accept_good_values():
    notes = review_notes(
        RECEIPT,
        {"company": "ABC SDN BHD", "date": "25/12/2018", "address": "NO 1, JALAN SAGU", "total": "RM 9.00"},
    )

    assert notes == {}
