from extractor.dataset import OcrLine
from extractor.schema import load_doctype
from extractor.fields.rules import (
    extract_fields,
    find_address,
    find_company,
    find_date,
    find_total,
    money,
)


def make_lines(*texts):
    return [OcrLine(text=text, box=(0,) * 8) for text in texts]


RECEIPT = [
    "TAN WOON YANN",
    "MR D.I.Y. (JOHOR) SDN BHD",
    "(CO.REG : 933109-X)",
    "LOT 1851-A & 1851-B, JALAN KPB 6,",
    "43300 SERI KEMBANGAN, SELANGOR",
    "-INVOICE-",
    "CHOPPING BOARD 1 X 33.92",
    "TOTAL RM 33.92",
    "TOTAL ROUNDED RM 33.90",
    "CASH RM 50.00",
    "CHANGE RM 16.10",
    "12-01-19 18:46 SH01 ZK10",
]


def test_extract_fields():
    receipt = extract_fields(make_lines(*RECEIPT))

    assert load_doctype("receipt").normalize(receipt) == {
        "company": "MR D.I.Y. (JOHOR) SDN BHD",
        "address": "LOT 1851-A & 1851-B, JALAN KPB 6, 43300 SERI KEMBANGAN, SELANGOR",
        "date": "2019-01-12",
        "total": "33.90",
    }


def test_company_prefers_business_words_over_first_line():
    assert find_company(["TAN WOON YANN", "ABC HO TRADING"]) == 1


def test_annotator_stamp_and_sdn_misread():
    receipt = extract_fields(make_lines("tan chay yee", "WARAKUYA PERMAS CITY SON BHD"))

    assert receipt["company"] == "WARAKUYA PERMAS CITY SDN BHD"


def test_company_joins_continuation_line():
    receipt = extract_fields(make_lines("POPULAR BOOK", "CO. (M) SDN BHD"))

    assert receipt["company"] == "POPULAR BOOK CO. (M) SDN BHD"


def test_address_skips_date_and_registration():
    texts = ["UNIHAKKA SDN BHD", "10 MAR 2018 18:24", "(867388-U)", "12, JALAN TAMPOI 7/4", "TAX INVOICE"]

    assert find_address(texts, 0) == "12, JALAN TAMPOI 7/4"


def test_address_stops_at_phone_number():
    texts = ["ABC TRADING", "81100 JOHOR BAHRU", "07-355 2616"]

    assert find_address(texts, 0) == "81100 JOHOR BAHRU"


def test_find_date_rejects_implausible_year():
    assert find_date(["1 MAR 2048 18:25", "DATE 01/03/2018"]) == "01/03/2018"


def test_company_falls_back_to_first_text_line():
    assert find_company(["***", "KEDAI MAKAN", "TAX INVOICE"]) == 1


def test_company_none_without_text():
    assert find_company(["1234", "--"]) is None


def test_address_stops_at_phone_line():
    texts = ["ABC TRADING", "NO.2&4, JALAN HARMONI 3/2,", "81100 JOHOR", "TEL: 07-355 2616"]

    assert find_address(texts, 0) == "NO.2&4, JALAN HARMONI 3/2, 81100 JOHOR"


def test_address_needs_a_digit():
    assert find_address(["ABC TRADING", "WELCOME", "TEL 07"], 0) is None


def test_find_date_skips_amounts_and_times():
    assert find_date(["TOTAL 12.50", "TIME 10:32:00", "DATE: 25/12/2018"]) == "25/12/2018"


def test_find_date_month_name():
    assert find_date(["25 DEC 2018 10:32"]) == "25 DEC 2018"


def test_money():
    assert money("RM 1,234.50 X 9,00") == ["1234.50", "9.00"]
    assert money("TOTAL 49, 60 / 64. 15") == ["49.60", "64.15"]


def test_total_ignores_gst_summary():
    assert find_total(["TOTAL 8.20", "GST SUMMARY AMOUNT 7.74"]) == "8.20"


def test_total_strong_keyword_wins():
    assert find_total(["TOTAL 33.92", "ROUNDING -0.02", "TOTAL ROUNDED 33.90"]) == "33.90"


def test_total_ignores_subtotal_and_quantity():
    assert find_total(["SUB TOTAL 40.00", "TOTAL QTY: 2.00", "TOTAL 42.40"]) == "42.40"


def test_total_amount_on_next_line():
    assert find_total(["GRAND TOTAL :", "20.00"]) == "20.00"


def test_total_fallback_skips_cash():
    assert find_total(["ITEM 5.00", "ITEM 7.50", "CASH 50.00"]) == "7.50"


def test_total_none():
    assert find_total(["THANK YOU"]) is None
