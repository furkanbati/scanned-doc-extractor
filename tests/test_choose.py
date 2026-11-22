"""Choosing a rule set by its fingerprint. Made-up formats."""

from extractor.ocr.base import OcrLine
from extractor.templates.choose import choose

ABC = {"name": "ABC", "fingerprint": ["ABC GIDA SANAYI", "Organize Sanayi Bolgesi Konya", "Genel Toplam",
                                      "Bu fatura elektronik ortamda olusturulmustur"]}
XYZ = {"name": "XYZ", "fingerprint": ["XYZ TEKSTIL LTD", "Ataturk Caddesi Izmir", "Genel Toplam",
                                      "Tesekkur ederiz"]}
# Shares ABC's lines, and prints two more of its own.
ABC_LONG = {"name": "ABC long", "fingerprint": ABC["fingerprint"] + ["Banka IBAN TR00 0000", "Vade 30 gun"]}


def lines(*texts):
    return [OcrLine(text=text, box=(0, 0, 1, 0, 1, 1, 0, 1)) for text in texts]


def test_the_format_whose_fixed_lines_are_printed_is_chosen():
    document = lines("ABC GIDA SANAYI", "Organize Sanayi Bolgesi Konya", "Fatura No: 171", "Genel Toplam", "1.250,00",
                     "Bu fatura elektronik ortamda olusturulmustur")

    match = choose(document, {"abc": ABC, "xyz": XYZ})

    assert match.file == "abc"
    assert (match.found, match.fixed) == (4, 4)
    assert match.to_json() == {"rule_set": "abc", "name": "ABC", "found": 4, "fixed": 4}


def test_a_shared_line_alone_does_not_make_a_format():
    document = lines("KLM ELEKTRIK", "Genel Toplam", "99,00")

    assert choose(document, {"abc": ABC, "xyz": XYZ}) is None


def test_misread_letters_still_match():
    document = lines("ABC GIDA SANAYl", "Organize Sanayi Bolgesi Konya", "Genel Toplam",
                     "Bu fatura elektronik ortamda olusturulmustvr")

    assert choose(document, {"abc": ABC, "xyz": XYZ}).file == "abc"


def test_on_an_equal_share_the_longer_fingerprint_wins():
    document = lines(*ABC_LONG["fingerprint"])

    assert choose(document, {"abc": ABC, "abc-long": ABC_LONG}).file == "abc-long"


def test_a_higher_share_beats_a_longer_fingerprint():
    document = lines(*ABC["fingerprint"])

    assert choose(document, {"abc": ABC, "abc-long": ABC_LONG}).file == "abc"


def test_no_rule_sets_no_format():
    assert choose(lines("ABC GIDA SANAYI"), {}) is None
