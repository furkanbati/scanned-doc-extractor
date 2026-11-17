from decimal import Decimal

import pytest

from evaluation.cord import CordTruth, TruthItem
from evaluation.cord_metrics import error_kinds, is_number_error, score_receipt, summarize
from extractor.fields.line_items import LineItem, LineItemResult


def truth(amounts, total=None, subtotal=None) -> CordTruth:
    return CordTruth(
        items=[TruthItem(name=None, count=None, amount=None if a is None else Decimal(a)) for a in amounts],
        total=None if total is None else Decimal(total),
        subtotal=None if subtotal is None else Decimal(subtotal),
    )


def prediction(amounts, total=None, needs_review=False) -> LineItemResult:
    return LineItemResult(
        items=[LineItem(name="x", amount=Decimal(a)) for a in amounts],
        total=None if total is None else Decimal(total),
        needs_review=needs_review,
    )


def test_all_right():
    score = score_receipt(prediction([5000, 7000], 12000), truth([5000, 7000], 12000))

    assert score.required == 4 and score.correct == 4
    assert score.all_correct and not score.silent_error and score.errors == []


def test_in_order_matching_counts_a_shift_as_wrong():
    score = score_receipt(prediction([7000, 5000], 12000), truth([5000, 7000], 12000))

    assert score.amounts_correct == 0
    assert score.true_positives == 2
    assert "wrong_row" in score.errors


def test_missed_row_unflagged_is_a_silent_error():
    score = score_receipt(prediction([5000], 5000), truth([5000, 0], 5000))

    assert not score.count_ok
    assert score.silent_error
    assert "missed_row" in score.errors


def test_flagged_error_is_not_silent():
    score = score_receipt(prediction([5000], 5000, needs_review=True), truth([5000, 0], 5000))

    assert not score.silent_error


def test_label_without_total_requires_no_total():
    score = score_receipt(prediction([5000]), truth([5000]))

    assert score.total_ok is None
    assert score.required == 2 and score.all_correct


def test_subtotal_taken_as_item():
    kinds = error_kinds(prediction([5000, 7000, 12000], 12000), truth([5000, 7000], 12000, subtotal=12000))

    assert "subtotal_as_item" in kinds


def test_extra_row_that_is_not_a_sum():
    kinds = error_kinds(prediction([5000, 4000, 7000], 16000), truth([5000, 7000], 16000))

    assert kinds == ["extra_row"]


def test_ocr_error_when_amount_missing_from_text():
    kinds = error_kinds(prediction([5000], 5000), truth([5000], 5000), ocr_amounts={Decimal(5000)})
    assert "ocr" not in kinds

    kinds = error_kinds(prediction([6000], 6000), truth([5000], 5000), ocr_amounts={Decimal(6000)})
    assert "ocr" in kinds


@pytest.mark.parametrize(
    "predicted, expected, result",
    [(57000, 570000, True), (16500, 16600, True), (16500, 16500, False), (5000, 7000, True), (5000, 12000, False)],
)
def test_is_number_error(predicted, expected, result):
    assert is_number_error(Decimal(predicted), Decimal(expected)) is result


def test_summarize():
    scores = [
        score_receipt(prediction([5000], 5000), truth([5000], 5000)),
        score_receipt(prediction([5000], 9000, needs_review=True), truth([5000], 5000)),
        score_receipt(prediction([], None), truth([5000], 5000)),
    ]

    summary = summarize(scores, [1.0, 2.0, 3.0])

    assert summary["receipts"] == 3
    assert summary["required_value_accuracy"] == pytest.approx((3 + 2 + 0) / 9)
    assert summary["item_count_accuracy"] == pytest.approx(2 / 3)
    assert summary["needs_review_rate"] == pytest.approx(1 / 3)
    assert summary["silent_error_rate"] == pytest.approx(1 / 3)
    assert summary["amount_precision"] == pytest.approx(1.0)
    assert summary["amount_recall"] == pytest.approx(2 / 3)
    assert summary["seconds_per_receipt"] == pytest.approx(2.0)


def test_summarize_needs_receipts():
    with pytest.raises(ValueError):
        summarize([], [])
