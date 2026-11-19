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


def test_model_answers_are_cached_with_their_tokens(tmp_path):
    from types import SimpleNamespace

    from evaluation.cord_eval import e5_answer
    from extractor.fields.base import Usage

    class FakeE3:
        calls = 0

        def respond(self, image):
            self.calls += 1
            return '{"line_items": null}', Usage(input_tokens=1000, output_tokens=200)

    sample = SimpleNamespace(id="test-0001", image=lambda: None)
    cache, path, engine = {}, tmp_path / "e3" / "test.jsonl", FakeE3()

    content, _ = e5_answer(sample, cache, path, engine)
    e5_answer(sample, cache, path, engine)

    assert content == '{"line_items": null}'
    assert engine.calls == 1
    assert cache["test-0001"]["input_tokens"] == 1000
    assert path.read_text(encoding="utf-8").count("\n") == 1


def test_e3_cost_per_receipt_uses_the_engine_prices():
    from evaluation.cord_eval import usd_per_receipt

    # e3.yaml: $0.10 per million input tokens, $0.50 per million output tokens
    entries = [{"input_tokens": 1000, "output_tokens": 200}, {"input_tokens": 3000, "output_tokens": 600}]

    assert usd_per_receipt(entries) == (2000 * 0.10 + 400 * 0.50) / 1e6
    assert usd_per_receipt([{"seconds": 1}]) is None
    assert usd_per_receipt([]) is None
