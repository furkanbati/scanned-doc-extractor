import pytest

from extractor.schema import (
    DocumentType,
    load_doctype,
    normalize_amount,
    normalize_date,
    normalize_text,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  Book  Ta .K (Taman Daya)  sdn bhd ", "BOOK TA.K (TAMAN DAYA) SDN BHD"),
        ("NO.53 , JALAN SAGU 18 ,\nJOHOR .", "NO.53, JALAN SAGU 18, JOHOR."),
        ("( TAMAN DAYA )", "(TAMAN DAYA)"),
        ("   ", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("25/12/2018", "2018-12-25"),
        ("25-12-2018", "2018-12-25"),
        ("25.12.18", "2018-12-25"),
        ("2018-12-25", "2018-12-25"),
        ("12/28/2017", "2017-12-28"),
        ("03/04/2018", "2018-04-03"),
        ("20180304", "2018-03-04"),
        ("25032018", "2018-03-25"),
        ("DATE: 05/01/2019 10:32", "2019-01-05"),
        ("25 DEC 2018", "2018-12-25"),
        ("25-Dec-18", "2018-12-25"),
        ("DECEMBER 25, 2018", "2018-12-25"),
        ("not a date", "NOT A DATE"),
        (None, None),
    ],
)
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("9.00", "9.00"),
        ("9", "9.00"),
        ("RM 60.3", "60.30"),
        ("RM1,234.50", "1234.50"),
        ("TOTAL: 73.55", "73.55"),
        ("free", "FREE"),
        (None, None),
    ],
)
def test_normalize_amount(raw, expected):
    assert normalize_amount(raw) == expected


def test_receipt_doctype():
    receipt = load_doctype("receipt")

    assert receipt.name == "receipt"
    assert receipt.field_names == ["company", "address", "date", "total"]
    assert [field.type for field in receipt.fields] == ["text", "text", "date", "amount"]
    assert all(field.description for field in receipt.fields)


def test_normalize_by_field_type():
    receipt = load_doctype("receipt")

    assert receipt.normalize({"company": " abc  sdn bhd", "date": "25/12/2018", "total": "RM 9"}) == {
        "company": "ABC SDN BHD",
        "address": None,
        "date": "2018-12-25",
        "total": "9.00",
    }


def test_values_keeps_only_doctype_fields():
    receipt = load_doctype("receipt")

    assert receipt.values({"company": "ABC", "extra": "x"}) == {
        "company": "ABC",
        "address": None,
        "date": None,
        "total": None,
    }


def test_new_doctype_from_yaml(tmp_path):
    path = tmp_path / "invoice.yaml"
    path.write_text(
        "name: invoice\n"
        "fields:\n"
        "  - {name: invoice_no, type: text}\n"
        "  - {name: due, type: date}\n",
        encoding="utf-8",
    )

    invoice = load_doctype(path)

    assert invoice.field_names == ["invoice_no", "due"]
    assert invoice.normalize({"invoice_no": " inv-7 ", "due": "01/02/2019"}) == {
        "invoice_no": "INV-7",
        "due": "2019-02-01",
    }


def test_unknown_field_type_is_rejected():
    with pytest.raises(ValueError):
        DocumentType(name="x", fields=[{"name": "a", "type": "number"}])
