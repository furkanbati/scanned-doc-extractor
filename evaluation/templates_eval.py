"""User rule sets (extractor/templates) on SROIE: learn from a store's first
receipts, read the rest of that store's receipts with the learned rules.

    python -m evaluation.templates_eval --split dev --ocr    # E2's OCR once per receipt (free, slow)
    python -m evaluation.templates_eval --split dev          # learn, apply, summarize

SROIE has many receipts from the same store, the closest stand-in for "invoices
of the same format" a user would teach a rule set with. Stores are grouped by
their labelled company name; that grouping is only for the evaluation, the
rule sets never see it. For each store with at least MIN_GROUP receipts:

- the first receipts by id are the examples, with their labels standing in for
  the values a user would confirm (3, up to MAX_EXAMPLES when the self-check
  asks for more);
- every other receipt of that store is read with the rule set: is it accepted
  by the rule set's gate, and are its fields right;
- every receipt of the other stores is read with it too: a rule set must not
  accept a receipt of another format (its fingerprint must not match).

E2's OCR lines and line items are cached in data/sroie/e2cache/<split>.jsonl,
so tuning the rules does not rerun the OCR. Rules and thresholds are tuned on
the dev split; the test split is read once at the end.
"""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from PIL import Image

from evaluation.dataset import DEFAULT_ROOT, SPLITS, Sample, load_sroie
from evaluation.metrics import field_matches
from extractor.ocr.base import OcrLine
from extractor.pipeline import build_pipeline, load_engine
from extractor.review import review_notes
from extractor.schema import DocumentType, load_doctype, loose
from extractor.templates.learn import Example, learn
from extractor.templates.read import FINGERPRINT_SHARE, decide, extract

RESULTS = Path(__file__).parent / "results" / "templates"
MIN_GROUP = 5
EXAMPLES = 3
# Receipts a user would show before giving up on a rule set.
MAX_TRIES = 6


def groups(samples: list[Sample], minimum: int = MIN_GROUP) -> dict[str, list[Sample]]:
    """Receipts by store (labelled company name), stores with `minimum` or more."""

    by_store = defaultdict(list)
    for sample in samples:
        by_store[loose(sample.fields["company"]) or ""].append(sample)

    return {store: sorted(found, key=lambda s: s.id) for store, found in sorted(by_store.items())
            if store and len(found) >= minimum}


def cache_path(root: Path, split: str) -> Path:
    return root / "e2cache" / f"{split}.jsonl"


def load_cache(root: Path, split: str) -> dict[str, dict]:
    path = cache_path(root, split)
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as file:
        return {row["id"]: row for line in file if (row := json.loads(line))}


def cached_lines(row: dict) -> list[OcrLine]:
    return [OcrLine(text=line["text"], box=tuple(line["box"])) for line in row["lines"]]


def run_ocr(split: str, root: Path) -> Path:
    """E2's OCR lines and line items for every receipt of the evaluated stores."""

    config = load_engine("e2")
    pipeline = build_pipeline(config, load_doctype(config["doctype"]))
    path = cache_path(root, split)
    path.parent.mkdir(parents=True, exist_ok=True)
    done = load_cache(root, split)

    samples = [s for found in groups(load_sroie(root, split=split)).values() for s in found]
    for number, sample in enumerate(samples, start=1):
        if sample.id in done:
            continue
        with Image.open(sample.image_path) as image:
            image.load()
            start = time.perf_counter()
            result = pipeline.run(image)
            seconds = time.perf_counter() - start
        row = {
            "id": sample.id,
            "lines": [{"text": line.text, "box": list(line.box)} for line in result.lines],
            "line_items": result.line_items.to_json() if result.line_items is not None else None,
            "seconds": round(seconds, 3),
        }
        with path.open("a", encoding="utf-8", newline="") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        if number % 25 == 0:
            print(f"{number}/{len(samples)} receipts", flush=True)

    return path


def teach(store: str, samples: list[Sample], cache: dict[str, dict], doctype: DocumentType,
          examples: int = EXAMPLES, most: int = MAX_TRIES) -> tuple[dict | None, set[str], dict]:
    """The rule set learned from the store's first receipts, as a user would
    teach it: three; when the self-check names an example that does not fit
    the others, that one is replaced with the next receipt, else one more is
    added; at most `most` receipts are shown in all.

    Returns the rule set (or None), the ids of every receipt shown, and the
    last unstable fields.
    """

    shown = list(samples[:examples])
    used = {s.id for s in shown}
    waiting = list(samples[examples:])
    unstable = {}
    while True:
        learned = learn(store, doctype, [Example(cached_lines(cache[s.id]), s.fields) for s in shown])
        if learned.template is not None:
            return learned.template, used, {}
        unstable = learned.unstable
        if not waiting or len(used) >= most:
            return None, used, unstable
        following = waiting.pop(0)
        used.add(following.id)
        if learned.misfits:
            # The example named for most fields is replaced.
            misfit = max(set(learned.misfits.values()), key=list(learned.misfits.values()).count)
            shown[misfit] = following
        else:
            shown.append(following)


def read_one(template: dict, sample: Sample, row: dict, doctype: DocumentType,
             share: float, items_check: bool) -> dict:
    lines = cached_lines(row)
    fields = extract(template, doctype, lines)
    items_total = None
    if items_check and row["line_items"] and row["line_items"]["total"] is not None:
        from decimal import Decimal
        items_total = Decimal(row["line_items"]["total"])
    gate = decide(template, doctype, lines, review_notes(doctype, fields, items_total), share)

    return {"id": sample.id, "accepted": gate.accepted, "reasons": gate.reasons,
            "matches": field_matches(fields, sample.fields, doctype), "read": fields, "truth": sample.fields}


def evaluate(split: str, root: Path, share: float = FINGERPRINT_SHARE, items_check: bool = False) -> dict:
    doctype = load_doctype("receipt")
    cache = load_cache(root, split)
    stores = groups(load_sroie(root, split=split, doctype=doctype))
    missing = [s.id for found in stores.values() for s in found if s.id not in cache]
    if missing:
        raise SystemExit(f"{len(missing)} receipts have no cached OCR; run with --ocr")

    per_store, own, foreign = {}, [], []
    for store, samples in stores.items():
        template, used, unstable = teach(store, samples, cache, doctype)
        if template is None:
            per_store[store] = {"receipts": len(samples), "learned": False, "shown": len(used), "unstable": unstable}
            continue
        rest = [s for s in samples if s.id not in used]
        rows = [read_one(template, s, cache[s.id], doctype, share, items_check) for s in rest]
        own += [{"store": store, **row} for row in rows]
        others = [s for other, found in stores.items() if other != store for s in found]
        wrong_format = [read_one(template, s, cache[s.id], doctype, share, items_check) for s in others]
        foreign += [{"store": store, **row} for row in wrong_format if row["accepted"]]
        per_store[store] = {
            "receipts": len(samples), "learned": True, "shown": len(used),
            "examples": template["examples"], "read": len(rows),
            "accepted": sum(row["accepted"] for row in rows),
            "right_when_accepted": _mean([row for row in rows if row["accepted"]]),
            "right_all": _mean(rows),
            "rules": {name: rule["kind"] + (f" {' '.join(rule['label'])}" if "label" in rule else "")
                      for name, rule in template["fields"].items()},
            "fingerprint": len(template["fingerprint"]),
            "other_stores_accepted": sum(row["accepted"] for row in wrong_format),
        }

    accepted = [row for row in own if row["accepted"]]
    return {
        "split": split, "fingerprint_share": share, "items_check": items_check,
        "stores": len(stores), "learned": sum(s["learned"] for s in per_store.values()),
        "read": len(own), "accepted": len(accepted),
        "right_when_accepted": _mean(accepted), "right_all": _mean(own),
        "other_stores_accepted": len(foreign), "other_stores_examples": foreign[:10],
        "per_store": per_store,
        "receipts": own,
    }


def with_fallback(summary: dict, cache: dict[str, dict], results: Path = RESULTS.parent) -> dict:
    """The rule set, then E5 for what its gate rejects, on the receipts read;
    beside E5 and E2 alone on the same receipts.

    E5's and E2's answers are those of their own runs on this split
    (evaluation/results/e5/receipts.jsonl, chain/e2-<split>.jsonl); nothing is
    run again. Time: the rule set's OCR (E2's, cached) on every receipt, plus
    E5's measured time on those sent on.
    """

    split = summary["split"]
    e5_path = results / "e5" / "receipts.jsonl"
    e2_path = results / "chain" / f"e2-{split}.jsonl"
    if split != "test" or not e5_path.is_file() or not e2_path.is_file():
        return {}
    e5 = {row["id"]: row for line in e5_path.open(encoding="utf-8") if (row := json.loads(line))}
    e2 = {row["id"]: row for line in e2_path.open(encoding="utf-8") if (row := json.loads(line))}
    e5_seconds = json.loads((results / "e5" / "report.json").read_text(encoding="utf-8"))["seconds_per_receipt"]

    rows = summary["receipts"]
    chained = [row if row["accepted"] else e5[row["id"]] for row in rows]
    sent = sum(not row["accepted"] for row in rows)
    rule_seconds = sum(cache[row["id"]]["seconds"] for row in rows) / len(rows)

    return {
        "receipts": len(rows),
        "sent_to_e5": sent,
        "field_accuracy": _mean(chained),
        "e5_alone": _mean([e5[row["id"]] for row in rows]),
        "e2_alone": _mean([e2[row["id"]] for row in rows]),
        "seconds_per_receipt": round(rule_seconds + e5_seconds * sent / len(rows), 2),
        "e5_seconds_per_receipt": round(e5_seconds, 2),
    }


def _mean(rows: list[dict]) -> dict:
    if not rows:
        return {}
    fields = list(rows[0]["matches"])
    accuracy = {name: round(sum(row["matches"][name] for row in rows) / len(rows), 4) for name in fields}

    return {**accuracy, "mean": round(sum(accuracy.values()) / len(fields), 4)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", default="dev", choices=SPLITS)
    parser.add_argument("--ocr", action="store_true", help="cache E2's OCR for the evaluated stores first")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--share", type=float, default=FINGERPRINT_SHARE)
    parser.add_argument("--items-check", action="store_true", help="gate also on the line items' total")
    args = parser.parse_args()

    if args.ocr:
        run_ocr(args.split, args.root)
    summary = evaluate(args.split, args.root, args.share, args.items_check)
    summary["with_e5"] = with_fallback(summary, load_cache(args.root, args.split))
    RESULTS.mkdir(parents=True, exist_ok=True)
    # Per receipt, with the labels: kept out of git like every receipts file.
    with (RESULTS / f"{args.split}.receipts.jsonl").open("w", encoding="utf-8", newline="") as file:
        for row in summary.pop("receipts"):
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    (RESULTS / f"{args.split}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_store"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
