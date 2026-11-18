"""The review gate (extractor/gate.py) on CORD: how many receipts it lets
through without a look, and how many of those are wrong.

    python -m evaluation.cord_eval --mode e2e --split validation   # first
    python -m evaluation.cord_gate --mode e2e --split validation

Reads the per-receipt results cord_eval wrote (<mode>-<split>.receipts.jsonl)
and writes gate-<mode>-<split>.json next to them. No model is called: a
receipt the gate rejects is counted as "goes to a model or a person".

CORD labels only line items, and E2's field rules (company, date, address)
were written for SROIE's Malaysian receipts, so on CORD the gate sees the line
items only: no field review notes.
"""

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

from evaluation.cord import SPLITS
from evaluation.cord_eval import DEFAULT_OUTPUT, MODES
from extractor.gate import decide

LEVELS = {"standard": False, "strict": True}
E3_USD_PER_1000 = 0.3109  # extractor/engines/e3.yaml, measured on SROIE


def wilson(errors: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a rate measured on `total` documents."""

    if total == 0:
        return 0.0, 1.0
    rate = errors / total
    centre = (rate + z * z / (2 * total)) / (1 + z * z / total)
    spread = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / (1 + z * z / total)

    return max(0.0, centre - spread), min(1.0, centre + spread)


def gate_summary(records: list[dict], strict: bool) -> dict:
    total = len(records)
    right = [record["correct"] == record["required"] for record in records]
    decisions = [decide(record["prediction"], strict=strict) for record in records]

    accepted = [ok for ok, decision in zip(right, decisions) if decision.accepted]
    wrong_accepted = accepted.count(False)
    low, high = wilson(wrong_accepted, len(accepted))
    routed = total - len(accepted)

    reasons = Counter()
    for decision in decisions:
        # One count per kind of reason, numbers and names taken out.
        reasons.update({_kind(reason) for reason in decision.reasons})

    return {
        "receipts": total,
        "accepted": len(accepted),
        "accepted_rate": len(accepted) / total,
        "accepted_wrong": wrong_accepted,
        "accepted_error_rate": wrong_accepted / len(accepted) if accepted else 0.0,
        "accepted_error_rate_95": [low, high],
        # Of the receipts the rules got fully right, the share the gate let through.
        "right_accepted_share": accepted.count(True) / right.count(True) if any(right) else 0.0,
        "routed": routed,
        "routed_rate": routed / total,
        # Sending only the routed receipts to E3 instead of all of them.
        "e3_usd_per_1000": E3_USD_PER_1000 * routed / total,
        "reasons": dict(reasons.most_common()),
    }


def _kind(reason: str) -> str:
    """The reason with item names and numbers taken out, for counting."""

    return re.sub(r"-?\d[\d.]*", "N", re.sub(r"'[^']*'", "'...'", reason))


def run(mode: str, split: str, output: Path) -> dict:
    path = output / f"{mode}-{split}.receipts.jsonl"
    if not path.is_file():
        raise SystemExit(f"{path} is missing: run python -m evaluation.cord_eval --mode {mode} --split {split}")

    with path.open(encoding="utf-8") as file:
        records = [json.loads(line) for line in file]

    summary = {
        "mode": mode,
        "split": split,
        **{level: gate_summary(records, strict) for level, strict in LEVELS.items()},
    }
    (output / f"gate-{mode}-{split}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


def results_table(output: Path) -> str:
    lines = [
        "| Mode | Split | Gate | Receipts | Accepted | Wrong among accepted (95% CI) | Right receipts accepted | To model or person | E3 cost / 1000 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for mode in MODES:
        for split in SPLITS:
            path = output / f"gate-{mode}-{split}.json"
            if not path.is_file():
                continue
            summary = json.loads(path.read_text(encoding="utf-8"))
            for level in LEVELS:
                s = summary[level]
                low, high = s["accepted_error_rate_95"]
                lines.append(
                    f"| {mode} | {split} | {level} | {s['receipts']} | {s['accepted_rate']:.2f} | "
                    f"{s['accepted_wrong']}/{s['accepted']} = {s['accepted_error_rate']:.3f} ({low:.3f}-{high:.3f}) | "
                    f"{s['right_accepted_share']:.2f} | {s['routed_rate']:.2f} | ${s['e3_usd_per_1000']:.3f} |"
                )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--split", choices=SPLITS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--table", action="store_true", help="print the saved results as a markdown table")
    args = parser.parse_args()

    if args.table:
        print(results_table(args.output))
        return
    if not (args.mode and args.split):
        parser.error("--mode and --split are required (or --table)")

    summary = run(args.mode, args.split, args.output)
    for level in LEVELS:
        s = summary[level]
        print(
            f"{args.mode}/{args.split} {level}: accepted {s['accepted']}/{s['receipts']}, "
            f"wrong among accepted {s['accepted_wrong']} ({s['accepted_error_rate']:.3f}), "
            f"right receipts accepted {s['right_accepted_share']:.2f}"
        )


if __name__ == "__main__":
    main()
