import json

import pytest

from evaluation import compare
from evaluation.metrics import error_kind, loose


@pytest.mark.parametrize(
    "predicted, expected, kind",
    [
        ("TAMPOI, 81200", "TAMPOI, 81200", None),
        ("ABC", None, None),
        ("TAMPOI, 81200", "TAMPOI,81200", "format"),
        ("SDN.BHD.", "SDN. BHD.", "format"),
        ("UNIHAAKKA SDN BHD", "UNIHAKKA SDN BHD", "near"),
        ("78A, JALAN", "78G, JALAN", "near"),
        (None, "9.00", "missing"),
        ("TAN CHAY YEE", "ABC HO TRADING", "wrong"),
    ],
)
def test_error_kind(predicted, expected, kind):
    assert error_kind(predicted, expected) == kind


def test_loose_keeps_letters_and_digits():
    assert loose("No.2&4, Jalan 3/2") == "NO24JALAN32"
    assert loose(" - ") is None
    assert loose(None) is None


def write_experiment(results, name, rows, usd=None):
    directory = results / name
    directory.mkdir(parents=True)
    accuracy = {
        field: sum(row["matches"][field] for row in rows) / len(rows)
        for field in ("company", "total")
    }
    accuracy["mean"] = sum(accuracy.values()) / 2
    (directory / "report.json").write_text(json.dumps({
        "metadata": {"description": f"{name} test"},
        "field_accuracy": accuracy,
        "seconds_per_receipt": 1.5,
        "usd_per_receipt": usd,
    }), encoding="utf-8")
    (directory / "receipts.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def row(receipt, company, total, expected_company="ABC SDN BHD", expected_total="9.00"):
    return {
        "id": receipt,
        "matches": {"company": company == expected_company, "total": total == expected_total},
        "predicted": {"company": company, "total": total},
        "expected": {"company": expected_company, "total": expected_total},
    }


def test_comparison_report(tmp_path):
    write_experiment(tmp_path, "e1", [
        row("000", "ABC SDN BHD", "9.00"),
        row("001", "ABCSDN BHD", None),
    ])
    write_experiment(tmp_path, "e3", [
        row("000", "ABC SDN BHD", "9.00"),
        row("001", "XYZ", "8.00"),
    ], usd=0.0003)

    experiments = [compare.load_experiment(tmp_path / name) for name in ("e1", "e3")]
    experiments[0]["config"] = {"fields": "rules"}
    experiments[1]["config"] = {"fields": "openai_vision"}

    e1 = experiments[0]
    assert compare.error_counts(e1)["company"] == {"format": 1}
    assert compare.error_counts(e1)["total"] == {"missing": 1}
    # company: 1 exact + 1 format of 2; total: 1 of 2
    assert compare.loose_accuracy(e1)["mean"] == pytest.approx((1.0 + 0.5) / 2)
    assert compare.hardest_receipts(experiments[1]) == [("001", {"company": "wrong", "total": "near"})]

    report = compare.render(experiments)

    assert "| e1: e1 test | 0.500 | 0.500 | **0.500** | 0.750 | 1.50 | 0 | no |" in report
    assert "| 0.30 | yes |" in report
    assert "| e1 | company | 1 | 0 | 0 | 0 | 1 |" in report
