import re

from rapidfuzz.distance import Levenshtein

from extractor.schema import DocumentType, Fields

# Error kinds of one field, from closest to the label to furthest.
ERROR_KINDS = ("format", "near", "missing", "wrong")


def loose(value: str | None) -> str | None:
    """Letters and digits only, so spacing and punctuation do not count.

    The labels themselves are inconsistent here ("TAMPOI, 81200" next to
    "TAMPOI,81200"), which the exact match counts as an error.
    """

    if value is None:
        return None

    return re.sub(r"[^0-9A-Z]", "", value.upper()) or None


def error_kind(predicted: str | None, expected: str | None) -> str | None:
    """How a normalized prediction differs from its normalized label.

    None: exact match (or no label). "format": same letters and digits, only
    spacing or punctuation differ. "near": at most 2 letters or digits off, a
    misread character or a typo in the label. "missing": nothing predicted.
    "wrong": anything else.
    """

    if expected is None or predicted == expected:
        return None

    if predicted is None:
        return "missing"

    if loose(predicted) == loose(expected):
        return "format"

    if Levenshtein.distance(loose(predicted) or "", loose(expected) or "") <= 2:
        return "near"

    return "wrong"


def field_matches(
    prediction: Fields,
    truth: Fields,
    doctype: DocumentType,
) -> dict[str, bool]:
    """Normalized exact match per field. A missing truth value never matches."""

    predicted = doctype.normalize(prediction)
    expected = doctype.normalize(truth)

    return {
        field: expected[field] is not None and predicted[field] == expected[field]
        for field in doctype.field_names
    }


def field_accuracy(
    predictions: list[Fields],
    truths: list[Fields],
    doctype: DocumentType,
) -> dict[str, float]:
    """Accuracy per field over the documents, plus their mean under "mean"."""

    if len(predictions) != len(truths):
        raise ValueError("predictions and truths must have the same length")

    if not truths:
        raise ValueError("at least one document is required")

    matches = [field_matches(p, t, doctype) for p, t in zip(predictions, truths)]
    scores = {
        field: sum(match[field] for match in matches) / len(matches)
        for field in doctype.field_names
    }
    scores["mean"] = sum(scores.values()) / len(doctype.field_names)

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
