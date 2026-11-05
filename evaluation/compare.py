"""Compare the experiments that have results and write comparison.md.

Usage (inside the test container, after the experiments have run):

    python -m evaluation.compare
    python -m evaluation.compare --experiments e1 e2 e3

Reads evaluation/results/<name>/report.json and receipts.jsonl. The
per-receipt file is git-ignored (it carries dataset labels), so the
comparison can only be rebuilt where the experiments were run.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from evaluation.metrics import ERROR_KINDS, error_kind
from extractor.pipeline import ENGINE_DIR, load_engine

DEFAULT_RESULTS = Path(__file__).parent / "results"


def load_experiment(directory: Path) -> dict:
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    with (directory / "receipts.jsonl").open(encoding="utf-8") as file:
        rows = [json.loads(line) for line in file]

    return {"name": directory.name, "report": report, "rows": rows}


def field_names(experiment: dict) -> list[str]:
    return [field for field in experiment["report"]["field_accuracy"] if field != "mean"]


def error_counts(experiment: dict) -> dict[str, Counter]:
    """Per field, how many receipts fall in each error kind."""

    counts = {field: Counter() for field in field_names(experiment)}

    for row in experiment["rows"]:
        for field in counts:
            kind = error_kind(row["predicted"][field], row["expected"][field])
            if kind is not None:
                counts[field][kind] += 1

    return counts


def loose_accuracy(experiment: dict) -> dict[str, float]:
    """Accuracy when spacing and punctuation differences count as correct."""

    receipts = len(experiment["rows"])
    counts = error_counts(experiment)
    exact = experiment["report"]["field_accuracy"]

    scores = {
        field: exact[field] + counts[field]["format"] / receipts
        for field in counts
    }
    scores["mean"] = sum(scores.values()) / len(counts)

    return scores


CLOUD_EXTRACTORS = {"openai_vision"}


def leaves_machine(experiment: dict) -> str:
    """Whether the document is sent to a cloud API, from the experiment's config."""

    config = experiment.get("config") or load_engine(experiment["name"])

    return "yes" if config["fields"] in CLOUD_EXTRACTORS else "no"


def hardest_receipts(experiment: dict, minimum_errors: int = 2) -> list[tuple[str, dict]]:
    """Receipts with at least minimum_errors wrong fields, most errors first."""

    hardest = []
    for row in experiment["rows"]:
        kinds = {
            field: error_kind(row["predicted"][field], row["expected"][field])
            for field in field_names(experiment)
        }
        kinds = {field: kind for field, kind in kinds.items() if kind is not None}
        if len(kinds) >= minimum_errors:
            hardest.append((row["id"], kinds))

    return sorted(hardest, key=lambda item: (-len(item[1]), item[0]))


def render(experiments: list[dict]) -> str:
    fields = field_names(experiments[0])
    receipts = len(experiments[0]["rows"])

    lines = [
        "# Comparison",
        "",
        f"SROIE test split, {receipts} receipts. Field accuracy is the normalized exact "
        "match (`extractor/schema.py`). \"Ignoring spacing\" also counts a value as "
        "correct when only spaces or punctuation differ from the label, because the "
        "labels themselves are not consistent there (`TAMPOI, 81200` next to "
        "`TAMPOI,81200`).",
        "",
        "## Results",
        "",
        "| Experiment | " + " | ".join(fields) + " | Mean | Mean, ignoring spacing "
        "| s/receipt | USD per 1,000 | Leaves the machine |",
        "|---" * (len(fields) + 6) + "|",
    ]

    for experiment in experiments:
        report = experiment["report"]
        accuracy = report["field_accuracy"]
        usd = report.get("usd_per_receipt")
        cells = [
            f"{experiment['name']}: {report['metadata']['description']}",
            *(f"{accuracy[field]:.3f}" for field in fields),
            f"**{accuracy['mean']:.3f}**",
            f"{loose_accuracy(experiment)['mean']:.3f}",
            f"{report['seconds_per_receipt']:.2f}",
            f"{usd * 1000:.2f}" if usd is not None else "0",
            leaves_machine(experiment),
        ]
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## What kind of errors",
        "",
        "Every field that does not match its label, by kind: **format** (same letters "
        "and digits, only spacing or punctuation differ), **near** (at most 2 letters or "
        "digits off: a misread character, or a typo in the label), **missing** (nothing "
        "extracted) and **wrong** (anything else).",
        "",
        "| Experiment | Field | " + " | ".join(ERROR_KINDS) + " | Errors |",
        "|---" * (len(ERROR_KINDS) + 3) + "|",
    ]

    for experiment in experiments:
        for field, counts in error_counts(experiment).items():
            lines.append(
                f"| {experiment['name']} | {field} | "
                + " | ".join(str(counts[kind]) for kind in ERROR_KINDS)
                + f" | {sum(counts.values())} |"
            )

    best = max(experiments, key=lambda experiment: experiment["report"]["field_accuracy"]["mean"])
    hardest = hardest_receipts(best)
    lines += [
        "",
        f"## Hardest receipts for {best['name']}",
        "",
        f"{len(hardest)} receipts have at least 2 fields wrong in {best['name']}, the most "
        "accurate experiment. Receipt ids refer to `data/sroie`.",
        "",
        "| Receipt | " + " | ".join(fields) + " |",
        "|---" * (len(fields) + 1) + "|",
    ]

    for receipt, kinds in hardest[:20]:
        lines.append(
            f"| {receipt} | " + " | ".join(kinds.get(field, "") for field in fields) + " |"
        )

    lines.append("")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.compare",
        description="Compare experiment results on the test split.",
    )
    parser.add_argument("--experiments", nargs="*", default=None)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)

    return parser.parse_args()


def stale_measurements(name: str, report: dict, config: dict | None) -> list[str]:
    """Where the engine's `measured` block no longer matches its latest report.

    The product shows the numbers in extractor/engines/<name>.yaml, so they
    have to be copied there by hand after a rerun; this catches a forgotten copy.
    """

    if config is None:
        return []

    usd = report.get("usd_per_receipt")
    latest = {
        "receipts": report["receipts"],
        "field_accuracy": round(report["field_accuracy"]["mean"], 4),
        "seconds_per_document": round(report["seconds_per_receipt"], 2),
        "usd_per_1000": round(usd * 1000 if usd else 0.0, 4),
    }
    shown = config.get("measured") or {}

    problems = []
    for key, value in latest.items():
        current = shown.get(key)
        if key == "field_accuracy" and current is not None:
            current = round(current, 4)
        if current != value:
            problems.append(f"{name}: measured.{key} is {current}, the report says {value}")

    return problems


def engine_config(name: str) -> dict | None:
    path = ENGINE_DIR / f"{name}.yaml"

    return load_engine(path) if path.is_file() else None


def main() -> None:
    args = parse_args()
    names = args.experiments or sorted(
        path.name
        for path in args.results.iterdir()
        if (path / "report.json").is_file() and (path / "receipts.jsonl").is_file()
    )
    experiments = [load_experiment(args.results / name) for name in names]

    report = render(experiments)
    (args.results / "comparison.md").write_text(report, encoding="utf-8")
    print(report)

    for experiment in experiments:
        for problem in stale_measurements(experiment["name"], experiment["report"], engine_config(experiment["name"])):
            print(f"WARNING {problem}; update extractor/engines/{experiment['name']}.yaml", file=sys.stderr)


if __name__ == "__main__":
    main()
