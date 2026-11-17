"""Scores for the CORD line-item benchmark.

Per receipt, the required values are: the number of items (1), each item's
amount (one per labelled item, matched in order) and the total (1, when the
receipt's label has one). "Required-value accuracy" is the share of these
that are right. A receipt with a wrong required value that was not flagged
needs_review is a silent error: the number a client cares most about.
"""

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from evaluation.cord import CordTruth
from extractor.line_items import LineItemResult

ERROR_KINDS = (
    "ocr",
    "missed_row",
    "subtotal_as_item",
    "extra_row",
    "wrong_row",
    "number",
    "total",
)


@dataclass(frozen=True)
class ReceiptScore:
    count_ok: bool
    amounts_correct: int
    amounts_required: int
    total_ok: bool | None  # None: the label has no total
    needs_review: bool
    true_positives: int
    predicted: int
    labelled: int
    errors: list[str] = field(default_factory=list)

    @property
    def required(self) -> int:
        return 1 + self.amounts_required + (self.total_ok is not None)

    @property
    def correct(self) -> int:
        return int(self.count_ok) + self.amounts_correct + int(bool(self.total_ok))

    @property
    def all_correct(self) -> bool:
        return self.correct == self.required

    @property
    def silent_error(self) -> bool:
        return not self.all_correct and not self.needs_review


def matched_amounts(predicted: list[Decimal], labelled: list[Decimal | None]) -> int:
    """Amounts found regardless of order (multiset intersection)."""

    left = Counter(predicted)
    right = Counter(amount for amount in labelled if amount is not None)

    return sum((left & right).values())


def _digits(value: Decimal) -> str:
    return str(abs(value)).replace(".", "").rstrip("0")


def is_number_error(predicted: Decimal, expected: Decimal) -> bool:
    """Same digits read with a different scale or sign, or one digit off."""

    if predicted == expected:
        return False

    a, b = _digits(predicted), _digits(expected)
    if a == b:
        return True

    if len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1:
        return True

    return False


def error_kinds(
    prediction: LineItemResult,
    truth: CordTruth,
    ocr_amounts: set[Decimal] | None = None,
) -> list[str]:
    """What went wrong on one receipt, as zero or more ERROR_KINDS.

    ocr_amounts: every amount the OCR text contains (end-to-end mode only).
    A labelled amount missing from it is an OCR error: no rule could find it.
    """

    kinds = []
    predicted = prediction.amounts
    labelled = truth.amounts

    if ocr_amounts is not None:
        needed = [amount for amount in [*labelled, truth.total] if amount is not None]
        if any(amount not in ocr_amounts and -amount not in ocr_amounts for amount in needed):
            kinds.append("ocr")

    if len(predicted) < len(labelled):
        kinds.append("missed_row")

    if len(predicted) > len(labelled):
        # An extra amount that is a sum: the subtotal, the total, or the items above it.
        sums = {truth.subtotal, truth.total}
        running = Decimal(0)
        for amount in predicted:
            sums.add(running)
            running += amount
        sums.discard(None)
        sums.discard(Decimal(0))
        extra = Counter(predicted) - Counter(amount for amount in labelled if amount is not None)
        if any(amount in sums for amount in extra):
            kinds.append("subtotal_as_item")
        else:
            kinds.append("extra_row")

    in_order = sum(
        index < len(predicted) and expected is not None and predicted[index] == expected
        for index, expected in enumerate(labelled)
    )
    if matched_amounts(predicted, labelled) > in_order and len(predicted) == len(labelled):
        kinds.append("wrong_row")

    # Item amounts are compared position by position only when the rows line up.
    aligned = len(predicted) == len(labelled)
    if (aligned and any(
        expected is not None and is_number_error(got, expected)
        for got, expected in zip(predicted, labelled)
    )) or (
        truth.total is not None
        and prediction.total is not None
        and is_number_error(prediction.total, truth.total)
    ):
        kinds.append("number")

    if truth.total is not None and prediction.total != truth.total:
        kinds.append("total")

    return kinds


def score_receipt(
    prediction: LineItemResult,
    truth: CordTruth,
    ocr_amounts: set[Decimal] | None = None,
) -> ReceiptScore:
    predicted = prediction.amounts
    labelled = truth.amounts

    amounts_correct = sum(
        index < len(predicted) and expected is not None and predicted[index] == expected
        for index, expected in enumerate(labelled)
    )
    score = ReceiptScore(
        count_ok=len(predicted) == len(labelled),
        amounts_correct=amounts_correct,
        amounts_required=len(labelled),
        total_ok=None if truth.total is None else prediction.total == truth.total,
        needs_review=prediction.needs_review,
        true_positives=matched_amounts(predicted, labelled),
        predicted=len(predicted),
        labelled=sum(amount is not None for amount in labelled),
    )

    if not score.all_correct:
        score.errors.extend(error_kinds(prediction, truth, ocr_amounts))

    return score


def summarize(scores: list[ReceiptScore], seconds: list[float]) -> dict:
    if not scores:
        raise ValueError("no receipts to summarize")

    receipts = len(scores)
    totals = [score for score in scores if score.total_ok is not None]
    true_positives = sum(score.true_positives for score in scores)
    predicted = sum(score.predicted for score in scores)
    labelled = sum(score.labelled for score in scores)
    precision = true_positives / predicted if predicted else 0.0
    recall = true_positives / labelled if labelled else 0.0

    return {
        "receipts": receipts,
        "item_count_accuracy": sum(score.count_ok for score in scores) / receipts,
        "item_amount_accuracy": sum(score.amounts_correct for score in scores)
        / max(sum(score.amounts_required for score in scores), 1),
        "total_accuracy": sum(bool(score.total_ok) for score in totals) / max(len(totals), 1),
        "required_value_accuracy": sum(score.correct for score in scores)
        / sum(score.required for score in scores),
        "receipts_fully_correct": sum(score.all_correct for score in scores) / receipts,
        "needs_review_rate": sum(score.needs_review for score in scores) / receipts,
        "silent_error_rate": sum(score.silent_error for score in scores) / receipts,
        "amount_precision": precision,
        "amount_recall": recall,
        "amount_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "seconds_per_receipt": sum(seconds) / len(seconds) if seconds else 0.0,
        "errors": dict(Counter(kind for score in scores for kind in score.errors)),
    }
