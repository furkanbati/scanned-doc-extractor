import pytest

from extractor.schema import Receipt, normalize_date, normalize_text, normalize_total


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
def test_normalize_total(raw, expected):
    assert normalize_total(raw) == expected


def test_receipt_normalized():
    receipt = Receipt(company=" abc  sdn bhd", date="25/12/2018", total="RM 9")

    assert receipt.normalized() == {
        "company": "ABC SDN BHD",
        "address": None,
        "date": "2018-12-25",
        "total": "9.00",
    }
