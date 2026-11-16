import json
from decimal import Decimal

from evaluation.cord_eval import load_ocr_cache, results_table, text_amounts
from extractor.ocr.base import OcrLine, rect_box


def test_text_amounts_reads_every_number_unsigned():
    lines = [OcrLine("TEA 5,000", rect_box(0, 0, 10, 10)), OcrLine("DISC -1.000", rect_box(0, 20, 10, 30))]

    assert text_amounts(lines) == {Decimal(5000), Decimal(1000)}


def test_ocr_cache_round_trip(tmp_path):
    path = tmp_path / "validation.jsonl"
    path.write_text(json.dumps({"id": "validation-0001", "seconds": 1.5, "segments": [["TEA", [0] * 8]]}) + "\n")

    cache = load_ocr_cache(path)

    assert cache["validation-0001"]["seconds"] == 1.5
    assert load_ocr_cache(tmp_path / "missing.jsonl") == {}


def test_results_table(tmp_path):
    summary = {
        "receipts": 100,
        "item_count_accuracy": 0.9,
        "item_amount_accuracy": 0.95,
        "total_accuracy": 1.0,
        "required_value_accuracy": 0.955,
        "receipts_fully_correct": 0.87,
        "needs_review_rate": 0.23,
        "silent_error_rate": 0.04,
        "amount_f1": 0.96,
        "seconds_per_receipt": 0.001,
    }
    (tmp_path / "oracle-validation.json").write_text(json.dumps(summary))

    table = results_table(tmp_path).splitlines()

    assert len(table) == 3
    assert table[2].startswith("| oracle | validation | 100 | 0.900 | 0.950 | 1.000 | 0.955 |")
