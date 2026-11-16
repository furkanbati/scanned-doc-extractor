"""Line-item benchmark on CORD v2, in two modes.

    python -m evaluation.cord_eval --mode oracle --split validation
    python -m evaluation.cord_eval --mode e2e --split validation

- oracle: the dataset's own words and boxes stand in for OCR. Measures the
  rules alone. CORD annotates only the item and total words, so this input has
  no store name, address or footer: an upper bound for the rules.
- e2e: image -> RapidOCR -> rules, as the product would run. RapidOCR output
  is cached per split in data/cord/ocr/<split>.jsonl, so changing a rule does
  not rerun OCR; the cached OCR time still counts in seconds per receipt.

The summary goes to evaluation/results/cord/<mode>-<split>.json, the
per-receipt results (labels included) next to it as .receipts.jsonl, which is
not committed. Rules are developed on train and validation; test is run once.
"""

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path

from evaluation.cord import DEFAULT_ROOT, SPLITS, CordSample, load_cord
from evaluation.cord_metrics import ERROR_KINDS, score_receipt, summarize
from extractor.amounts import parse_amount
from extractor.line_items import extract_line_items
from extractor.ocr.base import OcrLine

DEFAULT_OUTPUT = Path(__file__).parent / "results" / "cord"
MODES = ("oracle", "e2e")


def load_ocr_cache(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}

    with path.open(encoding="utf-8") as file:
        return {entry["id"]: entry for line in file if (entry := json.loads(line))}


def ocr_lines(sample: CordSample, cache: dict[str, dict], cache_path: Path, engine) -> tuple[list[OcrLine], float]:
    """RapidOCR segments (not grouped into lines) and the seconds OCR took."""

    if sample.id not in cache:
        image = sample.image()
        start = time.perf_counter()
        segments = engine.read(image)
        seconds = time.perf_counter() - start

        cache[sample.id] = {
            "id": sample.id,
            "seconds": seconds,
            "segments": [[line.text, list(line.box)] for line in segments],
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(cache[sample.id], ensure_ascii=False) + "\n")

    entry = cache[sample.id]

    return [OcrLine(text=text, box=tuple(box)) for text, box in entry["segments"]], entry["seconds"]


def text_amounts(lines: list[OcrLine]) -> set[Decimal]:
    """Every amount the OCR read, for telling OCR errors from rule errors."""

    amounts = set()
    for line in lines:
        for word in line.text.split():
            value = parse_amount(word)
            if value is not None:
                amounts.add(abs(value))

    return amounts


def run(mode: str, split: str, root: Path, limit: int | None, output: Path) -> dict:
    cache_path = Path(root) / "ocr" / f"{split}.jsonl"
    cache = load_ocr_cache(cache_path) if mode == "e2e" else {}
    engine = None

    scores, seconds, records = [], [], []
    samples = load_cord(root, split, limit, images=mode == "e2e")

    for number, sample in enumerate(samples, start=1):
        if mode == "oracle":
            lines, ocr_seconds, ocr_amounts = sample.words, 0.0, None
        else:
            if engine is None and sample.id not in cache:
                from extractor.ocr.rapid import RapidOcr

                engine = RapidOcr(group_lines=False)
            lines, ocr_seconds = ocr_lines(sample, cache, cache_path, engine)
            ocr_amounts = text_amounts(lines)

        start = time.perf_counter()
        prediction = extract_line_items(lines)
        rule_seconds = time.perf_counter() - start

        score = score_receipt(prediction, sample.truth, ocr_amounts)
        scores.append(score)
        seconds.append(ocr_seconds + rule_seconds)
        records.append(
            {
                "id": sample.id,
                "prediction": prediction.to_json(),
                "truth": {
                    "amounts": [None if a is None else str(a) for a in sample.truth.amounts],
                    "names": [item.name for item in sample.truth.items],
                    "subtotal": None if sample.truth.subtotal is None else str(sample.truth.subtotal),
                    "total": None if sample.truth.total is None else str(sample.truth.total),
                },
                "correct": score.correct,
                "required": score.required,
                "errors": score.errors,
                "silent_error": score.silent_error,
            }
        )

        if number % 100 == 0:
            print(f"{number} receipts", flush=True)

    summary = {"mode": mode, "split": split, **summarize(scores, seconds)}

    output.mkdir(parents=True, exist_ok=True)
    name = f"{mode}-{split}" + (f"-limit{limit}" if limit else "")
    (output / f"{name}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (output / f"{name}.receipts.jsonl").open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    return summary


def format_summary(summary: dict) -> str:
    rows = [
        ("Receipts", summary["receipts"]),
        ("Item count exactly right", f"{summary['item_count_accuracy']:.3f}"),
        ("Item amounts right (in order)", f"{summary['item_amount_accuracy']:.3f}"),
        ("Total right", f"{summary['total_accuracy']:.3f}"),
        ("Required-value accuracy", f"{summary['required_value_accuracy']:.3f}"),
        ("Receipts fully right", f"{summary['receipts_fully_correct']:.3f}"),
        ("needs_review rate", f"{summary['needs_review_rate']:.3f}"),
        ("Silent error rate", f"{summary['silent_error_rate']:.3f}"),
        ("Amount P / R / F1", f"{summary['amount_precision']:.3f} / {summary['amount_recall']:.3f} / {summary['amount_f1']:.3f}"),
        ("Seconds per receipt", f"{summary['seconds_per_receipt']:.3f}"),
    ]
    errors = ", ".join(f"{kind} {summary['errors'].get(kind, 0)}" for kind in ERROR_KINDS)

    return "\n".join(f"{label:32} {value}" for label, value in rows) + f"\nErrors: {errors}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--split", choices=SPLITS, required=True)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    summary = run(args.mode, args.split, args.root, args.limit, args.output)
    print(f"{args.mode} / {args.split}")
    print(format_summary(summary))


if __name__ == "__main__":
    main()
