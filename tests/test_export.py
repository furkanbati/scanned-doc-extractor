import csv
import io
import json

from openpyxl import load_workbook

from extractor.review import review_notes
from extractor.schema import load_doctype
from ui.export import columns, line_item_rows, result_rows, to_csv, to_json, to_xlsx

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


LINE_ITEMS = {
    "items": [
        {"name": "ISI CAMPUR", "amount": "43636", "quantity": "1", "unit_price": "43636"},
        {"name": "AQUA BOTOL", "amount": "9000", "quantity": None, "unit_price": None},
    ],
    "subtotal": "52636",
    "tax": "5264",
    "discount": None,
    "service": None,
    "total": "57900",
    "needs_review": True,
    "reasons": ["total found by arithmetic, not by a TOTAL line"],
}
WITH_ITEMS = [
    {"file": "d.jpg", "result": {**response({"total": "57.900"}), "line_items": LINE_ITEMS}, "error": None},
    {
        "file": "e.jpg",
        "result": {
            **response({"total": "9.999,99"}),
            "line_items": {**LINE_ITEMS, "items": [{"name": "CAKE", "amount": "9999.99", "quantity": None, "unit_price": None}],
                           "needs_review": False, "reasons": []},
        },
        "error": None,
    },
    *RESULTS,
]


def test_line_item_rows_one_per_item_with_numbers():
    rows = line_item_rows(WITH_ITEMS)

    assert [(row["file"], row["item"], row["name"]) for row in rows] == [
        ("d.jpg", 1, "ISI CAMPUR"),
        ("d.jpg", 2, "AQUA BOTOL"),
        ("e.jpg", 1, "CAKE"),
    ]
    assert rows[0]["amount"] == 43636 and rows[0]["quantity"] == 1
    assert rows[1]["quantity"] is None
    assert rows[2]["amount"] == 9999.99
    assert rows[0]["check"] == "line items: total found by arithmetic, not by a TOTAL line"
    assert rows[2]["check"] == ""


def test_documents_without_line_items_give_no_item_rows():
    assert line_item_rows(RESULTS) == []


def test_a_receipt_whose_items_do_not_add_up_is_checked():
    rows = result_rows(WITH_ITEMS)

    assert rows[0]["check"] == "line items: total found by arithmetic, not by a TOTAL line"
    assert rows[1]["check"] == ""


def test_xlsx_has_a_line_items_sheet():
    workbook = load_workbook(io.BytesIO(to_xlsx(result_rows(WITH_ITEMS), line_item_rows(WITH_ITEMS))))

    assert workbook.sheetnames == ["Documents", "Line items"]
    items = workbook["Line items"]
    assert [cell.value for cell in items[1]] == ["file", "item", "name", "quantity", "unit_price", "amount", "check"]
    assert items["F2"].value == 43636
    assert items["A2"].fill.fgColor.rgb == "00FFF4CC"
    assert items["A4"].fill.fgColor.rgb != "00FFF4CC"


def test_xlsx_without_items_keeps_one_sheet():
    workbook = load_workbook(io.BytesIO(to_xlsx(result_rows(RESULTS), [])))

    assert workbook.sheetnames == ["Documents"]


def test_json_nests_items_under_their_document():
    rows = json.loads(to_json(result_rows(WITH_ITEMS), line_item_rows(WITH_ITEMS)))

    assert [entry["name"] for entry in rows[0]["line_items"]] == ["ISI CAMPUR", "AQUA BOTOL"]
    assert rows[0]["line_items"][0] == {"item": 1, "name": "ISI CAMPUR", "quantity": 1, "unit_price": 43636, "amount": 43636}
    assert rows[2]["line_items"] == []


GOOD = {"company": "ABC SDN BHD", "date": "25/12/2018", "address": "NO 1, JALAN SAGU", "total": "RM 33.90"}


def test_total_differing_from_the_line_items_is_checked():
    from decimal import Decimal

    notes = review_notes(RECEIPT, {**GOOD, "total": "10.00"}, line_items_total=Decimal(57900))

    assert notes == {"total": "differs from the line items' total 57900"}


def test_total_equal_to_the_line_items_passes():
    from decimal import Decimal

    assert review_notes(RECEIPT, GOOD, line_items_total=Decimal("33.90")) == {}
    # "57.900" is fifty-seven thousand nine hundred, as the line items read it.
    assert review_notes(RECEIPT, {**GOOD, "total": "57.900"}, line_items_total=Decimal(57900)) == {}


def test_no_comparison_without_line_items_or_without_a_total():
    from decimal import Decimal

    assert review_notes(RECEIPT, {**GOOD, "total": "10.00"}) == {}
    assert review_notes(RECEIPT, {**GOOD, "total": None}, line_items_total=Decimal(57900)) == {"total": "not found"}


GATED = [
    {
        "file": "f.jpg",
        "result": {
            **response({"total": "30.000"}),
            "line_items": {**LINE_ITEMS, "items": [{"name": "TEA", "amount": "25000", "quantity": None, "unit_price": None},
                                                  {"name": "Extra Pearl", "amount": "5000", "quantity": None, "unit_price": None}],
                           "needs_review": False, "reasons": []},
            "gate": {"accepted": False, "reasons": ["date not found", "line items: 'Extra Pearl' may be an add-on to the item above"]},
        },
        "error": None,
    },
    {
        "file": "g.jpg",
        "result": {**response({"total": "9.000"}), "line_items": {**LINE_ITEMS, "needs_review": False, "reasons": []},
                   "gate": {"accepted": True, "reasons": []}},
        "error": None,
    },
]


def test_with_a_gate_the_check_column_lists_its_reasons():
    rows = result_rows(GATED)

    assert rows[0]["check"] == "date not found; line items: 'Extra Pearl' may be an add-on to the item above"
    assert rows[1]["check"] == ""


def test_with_a_gate_item_rows_carry_only_the_line_item_reasons():
    rows = line_item_rows(GATED)

    assert rows[0]["check"] == "line items: 'Extra Pearl' may be an add-on to the item above"
    assert rows[-1]["check"] == ""
