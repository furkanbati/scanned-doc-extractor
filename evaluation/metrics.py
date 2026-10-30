from rapidfuzz.distance import Levenshtein

from extractor.schema import FIELDS, Receipt


def field_matches(prediction: Receipt, truth: Receipt) -> dict[str, bool]:
    """Normalized exact match per field. A missing truth value never matches."""

    predicted = prediction.normalized()
    expected = truth.normalized()

    return {
        field: expected[field] is not None and predicted[field] == expected[field]
        for field in FIELDS
    }


def field_accuracy(
    predictions: list[Receipt],
    truths: list[Receipt],
) -> dict[str, float]:
    """Accuracy per field over the receipts, plus their mean under "mean"."""

    if len(predictions) != len(truths):
        raise ValueError("predictions and truths must have the same length")

    if not truths:
        raise ValueError("at least one receipt is required")

    matches = [field_matches(p, t) for p, t in zip(predictions, truths)]
    scores = {
        field: sum(match[field] for match in matches) / len(matches)
        for field in FIELDS
    }
    scores["mean"] = sum(scores.values()) / len(FIELDS)

    return scores


def cer(prediction: str, reference: str) -> float:
    """Character error rate: edit distance divided by the reference length."""

    if not reference:
        return 0.0 if not prediction else 1.0

    return Levenshtein.distance(prediction, reference) / len(reference)


def corpus_cer(predictions: list[str], references: list[str]) -> float:
    """Total edit distance over total reference length, so long receipts weigh more."""

    if len(predictions) != len(references):
        raise ValueError("predictions and references must have the same length")

    total_length = sum(len(reference) for reference in references)
    if total_length == 0:
        raise ValueError("references are empty")

    distance = sum(
        Levenshtein.distance(prediction, reference)
        for prediction, reference in zip(predictions, references)
    )

    return distance / total_length
