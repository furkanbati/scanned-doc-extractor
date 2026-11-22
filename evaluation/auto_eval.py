"""Rule sets learned without a user, measured on a SROIE split as a stream.

    python -m evaluation.auto_eval --split dev --cache   # E2's OCR and E5 on every receipt (free, slow)
    python -m evaluation.auto_eval --split dev --choose  # choosing rule sets by fingerprint
    python -m evaluation.auto_eval --split dev           # replay the stream, summarize

The cache step reads every receipt of the split once with E2's OCR (the lines
a rule set reads) and with E5 (the teacher), and keeps E5's fields, line
items and gate decision, in data/sroie/autocache/<split>.jsonl. Resumable.
"""

import argparse
import json
import os
import time
from pathlib import Path

from PIL import Image

from evaluation import templates_eval
from evaluation.dataset import DEFAULT_ROOT, SPLITS, load_sroie
from extractor.gate import decide
from extractor.pipeline import build_pipeline, load_engine
from extractor.review import review_notes
from extractor.schema import load_doctype, loose
from extractor.templates.choose import choose

RESULTS = Path(__file__).parent / "results" / "auto"


def cache_path(root: Path, split: str) -> Path:
    return root / "autocache" / f"{split}.jsonl"


def load_cache(root: Path, split: str) -> dict[str, dict]:
    path = cache_path(root, split)
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as file:
        return {row["id"]: row for line in file if (row := json.loads(line))}


def run_cache(split: str, root: Path) -> Path:
    """E2's OCR (lines and segments) and E5's answer for every receipt."""

    doctype = load_doctype("receipt")
    e2 = build_pipeline(load_engine("e2"), doctype)
    e5 = build_pipeline(load_engine("e5"), doctype)
    path = cache_path(root, split)
    path.parent.mkdir(parents=True, exist_ok=True)
    done = load_cache(root, split)
    samples = load_sroie(root, split=split, doctype=doctype)

    for number, sample in enumerate(samples, start=1):
        if sample.id in done:
            continue
        with Image.open(sample.image_path) as image:
            image.load()
            segments = e2.ocr.read_segments(image)
            lines = e2.ocr.lines(segments)
            start = time.perf_counter()
            try:
                answer, error = e5.run(image), None
            except Exception as failure:  # a timeout or a malformed answer: no teacher here
                answer, error = None, f"{type(failure).__name__}: {failure}"
            seconds = time.perf_counter() - start
        row = {"id": sample.id,
               "lines": [{"text": line.text, "box": list(line.box)} for line in lines],
               "segments": [{"text": line.text, "box": list(line.box)} for line in segments],
               "e5": None, "e5_seconds": round(seconds, 3), "e5_error": error}
        if answer is not None:
            items = answer.line_items.to_json() if answer.line_items is not None else None
            review = review_notes(doctype, answer.fields,
                                  answer.line_items.total if answer.line_items is not None else None)
            row["e5"] = {"fields": doctype.values(answer.fields), "line_items": items,
                         "gate": decide(items, review).to_json()}
        with path.open("a", encoding="utf-8", newline="") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{number}/{len(samples)} receipts", flush=True)

    return path


def choosing(split: str, root: Path) -> dict:
    """Rule sets taught per store as a user would (evaluation/templates_eval.py),
    then every other receipt of the split given to the chooser: is it given
    its own store's rule set, another's, or none?

    Receipts of stores with five or more receipts have E2's OCR cached by the
    rule-set evaluation; the others are taken from this module's cache when
    it holds them (stores without a rule set, mostly one receipt each).
    """

    doctype = load_doctype("receipt")
    cache = templates_eval.load_cache(root, split)
    lines_of = {row_id: templates_eval.cached_lines(row) for row_id, row in cache.items()}
    lines_of.update({row_id: templates_eval.cached_lines(row) for row_id, row in load_cache(root, split).items()
                     if row_id not in lines_of})
    samples = load_sroie(root, split=split, doctype=doctype)
    stores = templates_eval.groups(samples)
    saved, used = {}, set()
    for store, found in stores.items():
        template, shown, _ = templates_eval.teach(store, found, cache, doctype)
        used |= shown
        if template is not None:
            saved[store] = template

    counts = {"own": 0, "other": 0, "none": 0, "no_rule_set_none": 0, "no_rule_set_other": 0}
    wrong = []
    for sample in samples:
        if sample.id in used or sample.id not in lines_of:
            continue
        store = loose(sample.fields["company"]) or ""
        match = choose(lines_of[sample.id], saved)
        chosen = match.file if match else None
        if store in saved:
            key = "own" if chosen == store else "none" if chosen is None else "other"
        else:
            key = "no_rule_set_none" if chosen is None else "no_rule_set_other"
        counts[key] += 1
        if key in ("other", "no_rule_set_other"):
            wrong.append({"id": sample.id, "store": store, "chosen": chosen, "found": match.found, "fixed": match.fixed})

    with_rule_set = counts["own"] + counts["other"] + counts["none"]
    without = counts["no_rule_set_none"] + counts["no_rule_set_other"]
    return {
        "split": split, "rule_sets": len(saved),
        "receipts_with_a_rule_set": with_rule_set, "receipts_without": without,
        **counts,
        "own_share": round(counts["own"] / with_rule_set, 4) if with_rule_set else None,
        "wrong_examples": wrong[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", default="dev", choices=[s for s in SPLITS if s != "test"])
    parser.add_argument("--data", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--cache", action="store_true", help="read every receipt with E2's OCR and E5")
    parser.add_argument("--choose", action="store_true", help="measure choosing the rule set by fingerprint")
    args = parser.parse_args()
    os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")

    if args.cache:
        print(f"Cached in {run_cache(args.split, args.data)}")
        return
    if args.choose:
        summary = choosing(args.split, args.data)
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"choose-{args.split}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                           encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return


if __name__ == "__main__":
    main()
