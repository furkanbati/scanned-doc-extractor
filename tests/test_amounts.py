from decimal import Decimal

import pytest

from extractor.amounts import is_money, is_number, parse_amount


@pytest.mark.parametrize(
    "text, expected",
    [
        ("25,000", "25000"),
        ("25.000", "25000"),
        ("1,591,600", "1591600"),
        ("1.591.600", "1591600"),
        ("99,999.99", "99999.99"),
        ("9.999,99", "9999.99"),
        ("12.50", "12.50"),
        ("16.5", "16.5"),
        ("Rp. 41.580", "41580"),
        ("Rp41,580", "41580"),
        ("@15.000", "15000"),
        ("15000", "15000"),
        ("0", "0"),
        ("-16,000", "-16000"),
        ("1,400 -", "-1400"),
        ("1,260-", "-1260"),
        ("(2,000)", "-2000"),
        ("16, 500", "16500"),
        ("2 x 15,000", "15000"),
        ("25.", "25"),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize("text", [None, "", "TOTAL", "-"])
def test_parse_amount_without_number(text):
    assert parse_amount(text) is None


def test_same_value_in_both_separator_styles():
    assert parse_amount("45,500") == parse_amount("45.500") == parse_amount("45500")


@pytest.mark.parametrize("token", ["16,500", "1.591.600", "9.999,99", "12.50", "Rp.41.580", "-2,000", "1,400-", "@15.000"])
def test_is_money(token):
    assert is_money(token)


@pytest.mark.parametrize("token", ["15000", "2", "TOTAL", "12/03/2018", "021-555", "x2", "16.5"])
def test_is_not_money(token):
    assert not is_money(token)


@pytest.mark.parametrize("token, expected", [("15000", True), ("0", True), ("2x", False), ("ITEM", False), ("16,500", True)])
def test_is_number(token, expected):
    assert is_number(token) is expected
