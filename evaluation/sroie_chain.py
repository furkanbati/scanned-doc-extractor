"""The engine chain on SROIE fields: E2 reads every receipt; the receipts its
review gate rejects are answered by a second engine (E3 or E5).

    python -m evaluation.sroie_chain --run    # E2 with its gate decisions (free, about 25 min)
    python -m evaluation.sroie_chain          # combine with E3 and E5, write the summary

The chain was first measured on CORD line items (reports/cord_e3_line_items.md),
where the gate sees the items. On SROIE it sees E2's field notes and line
items, but not a misread company or address, so the chain's field accuracy
can sit below the second engine's alone. This measures by how much.

E2 runs here with the API's checks (extractor/review.py, extractor/gate.py);
its results go to evaluation/results/chain/e2-<split>.jsonl. The second
engine's per-receipt matches come from evaluation/results/<engine>/receipts.jsonl,
written by `python -m evaluation.run --config e3` (and e5): it is not run again.
"""

import argparse
import json
import time
from pathlib import Path

from PIL import Image

from evaluation.dataset import DEFAULT_ROOT, SPLITS, load_sroie
from evaluation.metrics import field_matches
from extractor.gate import decide
from extractor.pipeline import build_pipeline, load_engine
from extractor.review import review_notes
from extractor.schema import load_doctype

RESULTS = Path(__file__).parent / "results"
SECONDS = ("e3", "e5")


def run_e2(split: str, root: Path, output: Path, limit: int | None = None) -> Path:
    """E2 on every receipt, with the gate's decision; resumes where it stopped."""

    config = load_engine("e2")
    doctype = load_doctype(config["doctype"])
    pipeline = build_pipeline(config, doctype)
    path = output / f"e2-{split}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if path.is_file():
        with path.open(encoding="utf-8") as file:
            done = {json.loads(line)["id"] for line in file}

    samples = load_sroie(root, split=split, limit=limit, doctype=doctype)
    for number, sample in enumerate(samples, start=1):
        if sample.id in done:
            continue
        with Image.open(sample.image_path) as image:
            image.load()
            start = time.perf_counter()
            result = pipeline.run(image)
            seconds = time.perf_counter() - start

        items = result.line_items.to_json() if result.line_items is not None else None
        review = review_notes(doctype, result.fields, result.line_items.total if result.line_items else None)
        gate = decide(items, review)
        row = {
            "id": sample.id,
            "matches": field_matches(result.fields, sample.fields, doctype),
            "accepted": gate.accepted,
            "reasons": gate.reasons,
            "seconds": round(seconds, 3),
        }
        with path.open("a", encoding="utf-8", newline="") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        if number % 25 == 0:
            print(f"{number}/{len(samples)} receipts", flush=True)

    return path


def _rows(path: Path) -> dict[str, dict]:
    if not path.is_file():
        raise SystemExit(f"{path} is missing")
    with path.open(encoding="utf-8") as file:
        return {row["id"]: row for line in file if (row := json.loads(line))}


def _mean(rows: list[dict], fields: list[str]) -> dict[str, float]:
    if not rows:
        return {**{field: 0.0 for field in fields}, "mean": 0.0}
    accuracy = {field: sum(row["matches"][field] for row in rows) / len(rows) for field in fields}

    return {**accuracy, "mean": sum(accuracy.values()) / len(fields)}


def chain(first: dict[str, dict], second: dict[str, dict], second_report: dict) -> dict:
    """Field accuracy, share kept by E2, time and cost of E2 then the second engine."""

    missing = set(first) - set(second)
    if missing:
        raise SystemExit(f"the second engine's results lack {len(missing)} receipts")
    fields = list(next(iter(first.values()))["matches"])
    ids = sorted(first)
    kept = [i for i in ids if first[i]["accepted"]]
    sent = [i for i in ids if not first[i]["accepted"]]
    answers = [first[i] if first[i]["accepted"] else second[i] for i in ids]
    share = len(sent) / len(ids)
    usd = second_report.get("usd_per_receipt") or 0.0

    return {
        "receipts": len(ids),
        "kept_by_e2": len(kept),
        "sent_on": len(sent),
        "field_accuracy": _mean(answers, fields),
        "e2_on_kept": _mean([first[i] for i in kept], fields),
        "second_on_kept": _mean([second[i] for i in kept], fields),
        "second_alone": _mean([second[i] for i in ids], fields),
        "seconds_per_receipt": sum(first[i]["seconds"] for i in ids) / len(ids) + second_report["seconds_per_receipt"] * share,
        "usd_per_1000": usd * share * 1000,
    }


def summarize(split: str, output: Path, results: Path = RESULTS) -> dict:
    first = _rows(output / f"e2-{split}.jsonl")
    summary = {"split": split, "receipts": len(first), "kept_by_e2": sum(row["accepted"] for row in first.values())}
    suffix = "" if split == "test" else f"-{split}"
    for name in SECONDS:
        directory = results / f"{name}{suffix}"
        if not (directory / "receipts.jsonl").is_file():
            continue
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        summary[name] = chain(first, _rows(directory / "receipts.jsonl"), report)
    (output / f"summary-{split}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


def table(summary: dict) -> str:
    lines = [
        "| Chain | Kept by E2 | Field accuracy | Second engine alone | E2 on what it kept | Second engine on those | s/receipt | USD per 1,000 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in SECONDS:
        if name not in summary:
            continue
        s = summary[name]
        lines.append(
            f"| E2 -> {name.upper()} | {s['kept_by_e2']}/{s['receipts']} | {s['field_accuracy']['mean']:.3f} | "
            f"{s['second_alone']['mean']:.3f} | {s['e2_on_kept']['mean']:.3f} | {s['second_on_kept']['mean']:.3f} | "
            f"{s['seconds_per_receipt']:.1f} | {s['usd_per_1000']:.2f} |"
        )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", default="test", choices=SPLITS)
    parser.add_argument("--run", action="store_true", help="run E2 with its gate decisions first")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=RESULTS / "chain")
    args = parser.parse_args()

    if args.run:
        run_e2(args.split, args.root, args.output, args.limit)
    print(table(summarize(args.split, args.output)))


if __name__ == "__main__":
    main()
