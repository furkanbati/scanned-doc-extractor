"""Line-item rules on made-up receipts.

Each receipt is laid out the way an OCR engine reports it: one box per word
or segment, x to the right, y down. `row(y, (x, text), ...)` places words on
a line; amounts end at the same right edge, as on a printed receipt.
"""

import math
from decimal import Decimal

import pytest

from extractor.fields.line_items import (
    Row,
    Token,
    classify_row,
    extract_line_items,
    find_amount_column,
    group_rows,
    has_words,
    skew_angle,
    to_tokens,
)
from extractor.ocr.base import OcrLine, rect_box

HEIGHT = 20
CHAR = 10
RIGHT = 600  # where the amount column ends


def word(x: float, y: float, text: str) -> OcrLine:
    return OcrLine(text=text, box=rect_box(int(x), int(y), int(x + CHAR * len(text)), int(y + HEIGHT)))


def amount(y: float, text: str, right: int = RIGHT) -> OcrLine:
    return word(right - CHAR * len(text), y, text)


def row(y: float, *words: tuple[float, str], price: str | None = None) -> list[OcrLine]:
    lines = [word(x, y, text) for x, text in words]
    if price is not None:
        lines.append(amount(y, price))
    return lines


def receipt(*rows: list[OcrLine]) -> list[OcrLine]:
    return [line for r in rows for line in r]


def amounts(result) -> list[Decimal]:
    return [item.amount for item in result.items]


def D(value) -> Decimal:
    return Decimal(str(value))


# --- geometry ---------------------------------------------------------------


def test_words_on_one_line_form_one_row_left_to_right():
    lines = [word(300, 102, "TEA"), word(50, 100, "ICE"), word(50, 140, "NEXT")]

    rows = group_rows(to_tokens(lines))

    assert [r.text for r in rows] == ["ICE TEA", "NEXT"]


def test_skewed_scan_keeps_rows_together():
    angle = math.radians(4)

    def rotated(x, y, text):
        width = CHAR * len(text)
        points = [(x, y), (x + width, y), (x + width, y + HEIGHT), (x, y + HEIGHT)]
        box = []
        for px, py in points:
            box += [round(px * math.cos(angle) - py * math.sin(angle)), round(px * math.sin(angle) + py * math.cos(angle))]
        return OcrLine(text=text, box=tuple(box))

    # Far-apart words of one line drift by ~35 px at 4 degrees: more than a line height.
    lines = [rotated(50, 100, "NASI GORENG"), rotated(520, 100, "25,000"), rotated(50, 130, "ES TEH"), rotated(520, 130, "5,000")]

    assert skew_angle(lines) == pytest.approx(angle, abs=0.01)
    rows = group_rows(to_tokens(lines, skew_angle(lines)))
    assert [r.text for r in rows] == ["NASI GORENG 25,000", "ES TEH 5,000"]


def test_segment_is_split_into_words_with_interpolated_positions():
    tokens = to_tokens([OcrLine(text="2 TEA 5,000", box=rect_box(0, 0, 110, 20))])

    assert [t.text for t in tokens] == ["2", "TEA", "5,000"]
    assert tokens[2].right == pytest.approx(110)
    assert tokens[0].left == 0 and tokens[1].left == pytest.approx(20)


def test_amount_column_is_found_by_right_edge_not_heading():
    rows = group_rows(
        to_tokens(
            receipt(
                row(100, (50, "NASI"), price="25,000"),
                row(130, (50, "TEH"), price="5,000"),
                row(160, (50, "PHONE"), (200, "021-555")),
                row(190, (50, "MIE"), price="18,500"),
            )
        )
    )

    column = find_amount_column(rows)

    assert column is not None
    assert column.right == pytest.approx(RIGHT)


def test_amount_column_with_ungrouped_amounts():
    rows = group_rows(to_tokens(receipt(row(100, (50, "FISH"), price="25000"), row(130, (50, "TEA"), price="5000"))))

    assert find_amount_column(rows).right == pytest.approx(RIGHT)


# --- row kinds --------------------------------------------------------------


@pytest.mark.parametrize(
    "text, kind",
    [
        ("SUBTOTAL 25,000", "subtotal"),
        ("SUB TOTAL 25,000", "subtotal"),
        ("SUB. TOTAL 25,000", "subtotal"),
        ("TOTAL 27,500", "total"),
        ("GRAND TOTAL 27,500", "total"),
        ("TOTAL BAYAR 31,900", "total"),
        ("Netto : 50,039", "total"),
        ("PB1 10% 2,500", "tax"),
        ("TAX 2,500", "tax"),
        ("SERVICE CHARGE 1,250", "service"),
        ("SVC CHG 6% 12,750", "service"),
        ("DISC -2,000", "discount"),
        ("TOTAL DISC 2,000", "discount"),
        ("CASH 50,000", "payment"),
        ("TUNAI 50,000", "payment"),
        ("CHANGE 22,500", "change"),
        ("KEMBALI 22,500", "change"),
        ("TOTAL QTY 3", "count"),
        ("ITEMS: 3", "count"),
        ("ITEM: 3 SUBTOTAL 24,500", "subtotal"),
        ("NASI GORENG 25,000", None),
        ("S/C 5% 1,250", "service"),
        ("1 SC/P SUPER SUPREME 47,273", None),
        ("P81 11,073", "tax"),
    ],
)
def test_classify_row(text, kind):
    assert classify_row(text) == kind


def test_currency_sign_is_not_a_word():
    assert not has_words("Rp. 25.000")
    assert has_words("Rp. TEA")


# --- items ------------------------------------------------------------------


def test_simple_receipt_reconciles():
    result = extract_line_items(
        receipt(
            row(100, (50, "1"), (80, "NASI"), (130, "GORENG"), price="25,000"),
            row(130, (50, "2"), (80, "ES"), (110, "TEH"), price="10,000"),
            row(170, (80, "SUBTOTAL"), price="35,000"),
            row(200, (80, "PB1"), (120, "10%"), price="3,500"),
            row(230, (80, "TOTAL"), price="38,500"),
            row(260, (80, "CASH"), price="50,000"),
            row(290, (80, "CHANGE"), price="11,500"),
        )
    )

    assert amounts(result) == [D(25000), D(10000)]
    assert [item.name for item in result.items] == ["NASI GORENG", "ES TEH"]
    assert result.items[1].quantity == 2
    assert result.subtotal == D(35000) and result.tax == D(3500) and result.total == D(38500)
    assert not result.needs_review, result.reasons


def test_amount_on_the_line_below_the_name():
    result = extract_line_items(
        receipt(
            row(100, (50, "AYAM"), (100, "BAKAR")),
            row(130, (100, "2"), (120, "x"), (140, "15,000"), price="30,000"),
            row(170, (50, "TOTAL"), price="30,000"),
        )
    )

    assert amounts(result) == [D(30000)]
    assert result.items[0].name == "AYAM BAKAR"
    assert result.items[0].quantity == 2 and result.items[0].unit_price == D(15000)
    assert not result.needs_review, result.reasons


def test_long_name_on_two_lines_is_one_item():
    result = extract_line_items(
        receipt(
            row(100, (50, "CHICKEN"), (130, "KATSU"), price="40,000"),
            row(130, (50, "CURRY"), (110, "UDON")),
            row(160, (50, "TEA"), price="8,000"),
            row(200, (50, "TOTAL"), price="48,000"),
        )
    )

    assert amounts(result) == [D(40000), D(8000)]
    assert result.items[0].name == "CHICKEN KATSU CURRY UDON"


def test_unlabelled_sum_ends_the_items():
    result = extract_line_items(
        receipt(
            row(100, (50, "A"), (70, "TEA"), price="5,000"),
            row(130, (50, "B"), (70, "MILK"), price="7,000"),
            row(160, (50, "C"), (70, "BREAD"), price="8,000"),
            row(200, price="20,000"),  # no label, only the sum
            row(230, (50, "CASH"), price="20,000"),
        )
    )

    assert amounts(result) == [D(5000), D(7000), D(8000)]
    assert result.total == D(20000)


def test_item_equal_to_the_sum_above_is_still_an_item_when_items_follow():
    result = extract_line_items(
        receipt(
            row(100, (50, "1"), (70, "DUMPLING"), price="9.000"),
            row(130, (50, "1"), (70, "JAMUR"), price="8.000"),
            row(160, (50, "1"), (70, "CAISIM"), price="7.000"),
            row(190, (50, "2"), (70, "LAPCHIONG"), (400, "12000"), price="24.000"),
            row(220, (50, "1"), (70, "BIHUN"), price="8.000"),
            row(260, (50, "SUBTOTAL"), price="56.000"),
            row(290, (50, "TOTAL"), price="56.000"),
        )
    )

    assert amounts(result) == [D(9000), D(8000), D(7000), D(24000), D(8000)]
    assert not result.needs_review, result.reasons


def test_amount_column_is_the_rightmost_even_when_quantities_have_decimals():
    result = extract_line_items(
        receipt(
            row(100, (50, "SPAGHETTI"), (350, "1.00"), (450, "48000"), price="48000"),
            row(130, (50, "KATSUDON"), (350, "1.00"), (450, "58000"), price="58000"),
            row(170, (50, "TOTAL"), price="106.000"),
        )
    )

    assert amounts(result) == [D(48000), D(58000)]


def test_same_price_twice_is_two_items_without_a_summary_after():
    result = extract_line_items(
        receipt(
            row(100, (50, "TEA"), price="5,000"),
            row(130, (50, "TEA"), price="5,000"),
            row(170, (50, "TOTAL"), price="10,000"),
        )
    )

    assert amounts(result) == [D(5000), D(5000)]


def test_discount_line_belongs_to_the_item_above():
    result = extract_line_items(
        receipt(
            row(100, (50, "PIZZA"), price="50,000"),
            row(130, (70, "DISC"), price="-5,000"),
            row(160, (50, "TEA"), price="5,000"),
            row(200, (50, "TOTAL"), price="50,000"),
        )
    )

    assert amounts(result) == [D(50000), D(5000)]
    assert result.items[0].discount == D(-5000)
    assert not result.needs_review, result.reasons


def test_negative_amount_is_not_an_item():
    result = extract_line_items(
        receipt(
            row(100, (50, "BREAD"), price="26,000"),
            row(130, (50, "AMOUNT"), price="-7,800"),
            row(170, (50, "TOTAL"), price="18,200"),
        )
    )

    assert amounts(result) == [D(26000)]
    assert not result.needs_review, result.reasons


def test_indented_line_is_a_modifier_and_flags_review():
    result = extract_line_items(
        receipt(
            row(100, (50, "MILK"), (100, "TEA"), price="25,000"),
            row(130, (90, "PEARL"), price="4,000"),
            row(160, (50, "LATTE"), price="30,000"),
            row(200, (50, "TOTAL"), price="59,000"),
        )
    )

    assert amounts(result) == [D(25000), D(30000)]
    assert result.items[0].modifiers == D(4000)
    assert result.needs_review


def test_line_without_quantity_among_quantity_lines_is_a_modifier():
    result = extract_line_items(
        receipt(
            row(100, (50, "1"), (70, "x"), (90, "CHOCO"), (150, "MT"), price="27,000"),
            row(130, (90, "PEARL"), price="4,000"),
            row(160, (50, "1"), (70, "x"), (90, "MANGO"), (150, "GT"), price="25,000"),
            row(200, (50, "TOTAL"), price="56,000"),
        )
    )

    assert amounts(result) == [D(27000), D(25000)]
    assert result.items[0].modifiers == D(4000)
    assert result.needs_review


def test_free_line_is_an_item_but_flags_review():
    result = extract_line_items(
        receipt(
            row(100, (50, "BUN"), price="22,000"),
            row(130, (50, "PLASTIC"), (130, "BAG"), price="0"),
            row(170, (50, "TOTAL"), price="22,000"),
        )
    )

    assert amounts(result) == [D(22000), D(0)]
    assert result.needs_review


def test_amount_computed_from_quantity_and_unit_when_not_printed():
    result = extract_line_items(
        receipt(
            row(100, (50, "BUN")),
            row(130, (150, "22.000"), (230, "x1"), price="22.000"),
            row(160, (50, "PLASTIC"), (130, "BAG")),
            row(190, (150, "0"), (170, "x1")),  # the amount "0" not read
            row(230, (50, "TOTAL"), price="22.000"),
        )
    )

    assert amounts(result) == [D(22000), D(0)]
    assert result.items[1].name == "PLASTIC BAG" and result.items[1].derived
    assert result.needs_review


def test_name_without_amount_below_the_last_item_flags_review():
    result = extract_line_items(
        receipt(
            row(100, (50, "BUN"), price="22.000"),
            row(130, (50, "PLASTIC"), (130, "BAG")),
            row(160, (150, "0"), (170, "x1"), (210, "C")),  # its amount "0" read as "C"
            row(200, (50, "TOTAL"), price="22.000"),
        )
    )

    assert amounts(result) == [D(22000)]
    assert result.needs_review


@pytest.mark.parametrize(
    "text, expected",
    [("0 x1", (1, 0)), ("15.000 x2", (2, 15000)), ("2 x 15.000", (2, 15000)), ("2 @ Rp 15.000", (2, 15000)), ("TEA 2", None)],
)
def test_quantity_times_unit(text, expected):
    from extractor.fields.line_items import quantity_times_unit

    found = quantity_times_unit(text)
    assert found == (None if expected is None else (D(expected[0]), D(expected[1])))


# --- reconciliation ---------------------------------------------------------


def test_items_not_matching_subtotal_flag_review():
    result = extract_line_items(
        receipt(
            row(100, (50, "TEA"), price="5,000"),
            row(130, (50, "MILK"), price="7,000"),
            row(170, (50, "SUBTOTAL"), price="15,000"),
            row(200, (50, "TOTAL"), price="15,000"),
        )
    )

    assert result.needs_review
    assert any("subtotal" in reason for reason in result.reasons)


def test_total_not_adding_up_flags_review():
    result = extract_line_items(
        receipt(
            row(100, (50, "TEA"), price="5,000"),
            row(140, (50, "TOTAL"), price="9,000"),
        )
    )

    assert result.needs_review


def test_tax_below_the_total_is_already_included():
    result = extract_line_items(
        receipt(
            row(100, (50, "BREAD"), price="25,900"),
            row(140, (50, "TOTAL"), price="25,900"),
            row(170, (50, "TUNAI"), price="25,900"),
            row(200, (50, "PPN"), price="2,355"),
        )
    )

    assert result.total == D(25900)
    assert not result.needs_review, result.reasons


def test_tax_base_line_is_not_tax():
    result = extract_line_items(
        receipt(
            row(100, (50, "RICE"), price="20,000"),
            row(140, (50, "SUBTOTAL"), price="20,000"),
            row(170, (50, "DASAR"), (120, "PAJAK"), price="20,000"),
            row(200, (50, "P.REST"), (130, "10%"), price="2,000"),
            row(230, (50, "TOTAL"), price="22,000"),
        )
    )

    assert result.tax == D(2000)
    assert result.total == D(22000)
    assert not result.needs_review, result.reasons


def test_rounded_total_still_adds_up():
    result = extract_line_items(
        receipt(
            row(100, (50, "FISH"), price="55,834"),
            row(140, (50, "TOTAL"), price="55,800"),
        )
    )

    assert result.total == D(55800)
    assert not result.needs_review, result.reasons


@pytest.mark.parametrize(
    "found, expected, result",
    [(55800, 55834, True), (32000, 31999, True), (121768, 121798, False), (55834, 55834, True), (50000, 55834, False)],
)
def test_adds_up_accepts_rounding_not_misreads(found, expected, result):
    from extractor.fields.line_items import adds_up

    assert adds_up(D(found), D(expected)) is result


def test_total_paid_wins_over_an_earlier_total_line():
    result = extract_line_items(
        receipt(
            row(100, (50, "FISH"), price="55,834"),
            row(140, (50, "TOTAL"), price="55,834"),
            row(170, (50, "TOTAL"), price="55,800"),
            row(200, (50, "CASH"), price="60,000"),
            row(230, (50, "CHANGE"), price="4,200"),
        )
    )

    assert result.total == D(55800)


def test_total_before_tax_is_a_subtotal_when_the_sum_is_printed_below():
    result = extract_line_items(
        receipt(
            row(100, (50, "ISI"), (90, "CAMPUR"), price="43.636"),
            row(130, (50, "AGUA"), price="9.000"),
            row(170, (50, "TOTAL"), price="52.636"),
            row(200, (50, "TAX"), (100, "10%"), price="5.264"),
            row(230, (50, "GR"), (80, "TTAL"), price="57.900"),  # TOTAL misread
            row(260, (50, "TUNAI"), price="60.000"),
        )
    )

    assert result.total == D(57900)
    assert result.subtotal == D(52636)
    assert result.needs_review  # found by arithmetic


def test_later_total_that_does_not_add_up_flags_review():
    result = extract_line_items(
        receipt(
            row(100, (50, "PASTA"), price="366.000"),
            row(140, (50, "TOTAL"), price="366.000"),
            row(170, (50, "SERY."), (110, "CHARGE"), price="18.300"),  # SERVICE misread
            row(200, (50, "TOTAL"), (110, "RUPIAH"), price="384.300"),
        )
    )

    assert result.needs_review


def test_missing_total_flags_review():
    result = extract_line_items(receipt(row(100, (50, "TEA"), price="5,000"), row(130, (50, "COFFEE"), price="9,000")))

    assert result.total is None
    assert result.needs_review


def test_no_text():
    result = extract_line_items([])

    assert result.items == [] and result.needs_review


def test_quantity_times_unit_price_mismatch_flags_review():
    result = extract_line_items(
        receipt(
            row(100, (50, "2"), (70, "x"), (90, "15,000"), (300, "SATE"), price="25,000"),
            row(140, (50, "TOTAL"), price="25,000"),
        )
    )

    assert result.needs_review
    assert any(" x " in reason for reason in result.reasons)


def test_row_dataclass_text():
    tokens = [Token("A", 0, 10, 0, 10), Token("B", 20, 30, 0, 10)]

    assert Row(tokens).text == "A B"
