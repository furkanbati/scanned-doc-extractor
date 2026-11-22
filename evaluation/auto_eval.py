"""Rule sets learned without a user, measured on a SROIE split as a stream.

    python -m evaluation.auto_eval --split dev --cache   # E2's OCR and E5 on every receipt (free, slow)
    python -m evaluation.auto_eval --split dev --choose  # choosing rule sets by fingerprint
    python -m evaluation.auto_eval --split dev --likeness  # grouping: same store vs different stores
    python -m evaluation.auto_eval --split dev           # replay the stream, summarize

The cache step reads every receipt of the split once with E2's OCR (the lines
a rule set reads) and with E5 (the teacher), and keeps E5's fields, line
items and gate decision, in data/sroie/autocache/<split>.jsonl. Resumable.
"""

import argparse
import json
import os
import random
import tempfile
import time
from pathlib import Path

from PIL import Image

from evaluation import templates_eval
from evaluation.dataset import DEFAULT_ROOT, SPLITS, load_sroie
from evaluation.metrics import field_matches
from extractor.gate import decide
from extractor.pipeline import build_pipeline, load_engine
from extractor.review import review_notes
from extractor.schema import load_doctype, loose
from extractor.templates import auto, store
from extractor.templates import read as template_read
from extractor.templates.choose import choose

# The order receipts arrive in: shuffled, so a store's receipts are spread.
STREAM_SEED = 7

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


def likeness(split: str, root: Path) -> dict:
    """auto.format_likeness for every pair of receipts of the stores with five
    or more: how alike one store's receipts are, and how alike different
    stores' are, to set auto.GROUP_ALIKE between them."""

    import itertools

    doctype = load_doctype("receipt")
    cache = templates_eval.load_cache(root, split)
    stores = templates_eval.groups(load_sroie(root, split=split, doctype=doctype))
    items = [(store, templates_eval.cached_lines(cache[s.id])) for store, found in stores.items() for s in found]
    same, different = [], []
    for (first_store, first), (second_store, second) in itertools.combinations(items, 2):
        (same if first_store == second_store else different).append(auto.format_likeness(first, second))

    return {
        "split": split, "same_store_pairs": len(same), "different_store_pairs": len(different),
        "same_min": round(min(same), 3), "different_max": round(max(different), 3),
        "by_threshold": {str(threshold): {"same_store_split": sum(value < threshold for value in same),
                                          "different_stores_joined": sum(value >= threshold for value in different)}
                         for threshold in (0.25, 0.3, 0.4, 0.5, 0.6)},
        "group_alike": auto.GROUP_ALIKE,
    }


def stream(split: str, root: Path, seed: int = STREAM_SEED, teacher_check: str = "gate") -> dict:
    """Every receipt of the split in turn, as `/extract` with learning=auto
    would treat it, with E5 as the teacher (its cached answers).

    A receipt of a known format is read by its rule set; if the rule set's
    gate rejects it, E5's answer stands. On probation, E5's answer is
    compared, and a disagreement removes the rule set (E5's answer stands).
    A receipt of no known format gets E5's answer, and becomes an example
    when E5's gate accepted it.

    `teacher_check`: "gate", as the API (fields, and the line items adding
    up); "fields", a looser variant measured beside it: every field found and
    well formed, the items not looked at.
    """

    doctype = load_doctype("receipt")
    cache = load_cache(root, split)
    samples = [s for s in load_sroie(root, split=split, doctype=doctype) if s.id in cache]
    random.Random(seed).shuffle(samples)
    os.environ["TEMPLATES_DIR"] = tempfile.mkdtemp(prefix="auto-eval-")

    rows, removed, probation_checks, accepted_by_teacher = [], [], 0, 0
    for sample in samples:
        row = cache[sample.id]
        lines = templates_eval.cached_lines(row)
        teacher = row["e5"]
        teacher_fields = teacher["fields"] if teacher else {name: None for name in doctype.field_names}
        if teacher_check == "fields":
            teacher_ok = bool(teacher) and not review_notes(doctype, teacher_fields)
        else:
            teacher_ok = bool(teacher and teacher["gate"]["accepted"])
        accepted_by_teacher += teacher_ok
        match = choose(lines, store.all_templates())
        source, final, learned = "e5", teacher_fields, None
        if match is not None:
            read = template_read.extract(match.template, doctype, lines)
            gate = template_read.decide(match.template, doctype, lines, review_notes(doctype, read))
            if not gate.accepted:
                source = "e5 (rule set rejected it)"
            elif auto.on_probation(match.template) and teacher_ok:
                probation_checks += 1
                checked = auto.check(match.file, match.template, doctype, read, teacher_fields)
                if checked["removed"]:
                    removed.append({"rule_set": match.file, "id": sample.id, "differ": checked["differ"]})
                    source = "e5 (rule set removed)"
                else:
                    source, final = "rule set", read
            else:
                source, final = "rule set", read
        elif teacher_ok:
            learned = auto.observe(lines, teacher_fields, doctype, "e5").get("learned")
        rows.append({"id": sample.id, "store": loose(sample.fields["company"]) or "", "source": source,
                     "rule_set": match.file if match else None, "learned": learned,
                     "matches": field_matches(final, sample.fields, doctype),
                     "e5_matches": field_matches(teacher_fields, sample.fields, doctype),
                     "e5_seconds": row["e5_seconds"]})

    def right(chosen, key="matches"):
        cells = [value for row in chosen for value in row[key].values()]
        return round(sum(cells) / len(cells), 4) if cells else None

    by_rules = [row for row in rows if row["source"] == "rule set"]
    learned = [row["learned"] for row in rows if row["learned"]]
    # The store a rule set was learned for: that of the receipt completing its group.
    learned_for = {row["learned"]: row["store"] for row in rows if row["learned"]}
    wrong_store = [row for row in by_rules if learned_for.get(row["rule_set"]) != row["store"]]
    e5_calls = sum(row["source"] != "rule set" for row in rows) + probation_checks
    return {
        "split": split, "receipts": len(rows), "stream_seed": seed, "teacher_check": teacher_check,
        "teacher_accepted": accepted_by_teacher,
        "rule_sets_learned": len(learned), "removed_on_probation": removed,
        "read_by_rule_sets": len(by_rules), "e5_calls": e5_calls, "probation_checks": probation_checks,
        "right_all": right(rows), "right_all_e5_alone": right(rows, "e5_matches"),
        "right_by_rule_sets": right(by_rules), "right_by_rule_sets_e5_on_same": right(by_rules, "e5_matches"),
        "seconds_e5_saved": round(sum(row["e5_seconds"] for row in by_rules), 1),
        "rule_set_reads_of_another_store": len(wrong_store),
        "learned": learned,
        "receipts_detail": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", default="dev", choices=[s for s in SPLITS if s != "test"])
    parser.add_argument("--data", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--cache", action="store_true", help="read every receipt with E2's OCR and E5")
    parser.add_argument("--choose", action="store_true", help="measure choosing the rule set by fingerprint")
    parser.add_argument("--likeness", action="store_true", help="format likeness, same vs different stores")
    parser.add_argument("--teacher-check", default="gate", choices=["gate", "fields"],
                        help="when a teacher's answer becomes an example (gate: as the API)")
    args = parser.parse_args()
    os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")

    if args.cache:
        print(f"Cached in {run_cache(args.split, args.data)}")
        return
    if args.likeness:
        summary = likeness(args.split, args.data)
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"likeness-{args.split}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return
    if args.choose:
        summary = choosing(args.split, args.data)
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"choose-{args.split}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                           encoding="utf-8")
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    summary = stream(args.split, args.data, teacher_check=args.teacher_check)
    name = f"stream-{args.split}" + ("" if args.teacher_check == "gate" else f"-{args.teacher_check}")
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / f"{name}.receipts.jsonl").open("w", encoding="utf-8", newline="") as file:
        for row in summary.pop("receipts_detail"):
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    (RESULTS / f"{name}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                       encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
