import pytest

from evaluation.metrics import cer, corpus_cer, field_accuracy, field_matches
from extractor.schema import Receipt

TRUTH = Receipt(
    company="ABC SDN BHD",
    address="NO.1, JALAN SAGU, JOHOR.",
    date="25/12/2018",
    total="9.00",
)


def test_field_matches_uses_normalization():
    prediction = Receipt(
        company="abc  sdn bhd",
        address="NO.1 , JALAN SAGU , JOHOR .",
        date="25-12-18",
        total="RM 9",
    )

    assert field_matches(prediction, TRUTH) == {
        "company": True,
        "address": True,
        "date": True,
        "total": True,
    }


def test_field_matches_wrong_and_missing():
    prediction = Receipt(company="ABC SDN BHD", date="26/12/2018")

    assert field_matches(prediction, TRUTH) == {
        "company": True,
        "address": False,
        "date": False,
        "total": False,
    }


def test_missing_truth_never_matches():
    assert field_matches(Receipt(), Receipt())["company"] is False


def test_field_accuracy_per_field_and_mean():
    predictions = [
        TRUTH,
        Receipt(company="ABC SDN BHD", total="9.00"),
    ]

    scores = field_accuracy(predictions, [TRUTH, TRUTH])

    assert scores == {
        "company": 1.0,
        "address": 0.5,
        "date": 0.5,
        "total": 1.0,
        "mean": 0.75,
    }


def test_field_accuracy_rejects_bad_input():
    with pytest.raises(ValueError):
        field_accuracy([TRUTH], [])

    with pytest.raises(ValueError):
        field_accuracy([], [])


@pytest.mark.parametrize(
    "prediction, reference, expected",
    [
        ("TOTAL 9.00", "TOTAL 9.00", 0.0),
        ("TOTAL 9.0O", "TOTAL 9.00", 0.1),
        ("TOTA 9.00", "TOTAL 9.00", 0.1),
        ("", "ABCD", 1.0),
        ("ABCDEFGH", "ABCD", 1.0),
        ("", "", 0.0),
        ("A", "", 1.0),
    ],
)
def test_cer(prediction, reference, expected):
    assert cer(prediction, reference) == pytest.approx(expected)


def test_corpus_cer_weighs_by_reference_length():
    # 1 error in 10 characters and 1 error in 2 characters: 2 / 12.
    assert corpus_cer(["TOTAL 9.0O", "AC"], ["TOTAL 9.00", "AB"]) == pytest.approx(
        2 / 12
    )


def test_corpus_cer_rejects_bad_input():
    with pytest.raises(ValueError):
        corpus_cer(["A"], [])

    with pytest.raises(ValueError):
        corpus_cer([""], [""])
