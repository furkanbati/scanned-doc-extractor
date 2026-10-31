"""Run one experiment config on a SROIE split and write its report.

Usage (inside the test container):

    python -m evaluation.run --config e1
    python -m evaluation.run --config e1 --split dev --limit 50
"""

import argparse
import json
import platform
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from PIL import Image

from evaluation.metrics import corpus_cer, field_accuracy, field_matches
from extractor.dataset import DEFAULT_ROOT, SPLITS, Sample, load_sroie
from extractor.ocr.base import lines_to_text
from extractor.pipeline import Pipeline, build_fields, build_pipeline
from extractor.schema import DocumentType, Fields, load_doctype

CONFIG_DIR = Path(__file__).parent / "configs"
DEFAULT_OUTPUT = Path(__file__).parent / "results"


@dataclass(frozen=True)
class SampleResult:
    sample: Sample
    prediction: Fields
    ocr_text: str
    seconds: float


def load_config(name: str) -> dict:
    path = Path(name)
    if not path.is_file():
        path = CONFIG_DIR / f"{name}.yaml"

    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def run_samples(pipeline: Pipeline, samples: list[Sample]) -> list[SampleResult]:
    results = []

    for number, sample in enumerate(samples, start=1):
        with Image.open(sample.image_path) as image:
            image.load()
            start = time.perf_counter()
            output = pipeline.run(image)
            seconds = time.perf_counter() - start

        results.append(
            SampleResult(
                sample=sample,
                prediction=output.fields,
                ocr_text=lines_to_text(output.lines),
                seconds=seconds,
            )
        )

        if number % 25 == 0 or number == len(samples):
            print(f"{number}/{len(samples)} receipts", flush=True)

    return results


def summarize(
    results: list[SampleResult],
    doctype: DocumentType,
    fields_from_labels: dict | None,
) -> dict:
    truths = [result.sample.fields for result in results]
    seconds = [result.seconds for result in results]

    return {
        "receipts": len(results),
        "field_accuracy": field_accuracy(
            [result.prediction for result in results], truths, doctype
        ),
        "cer": corpus_cer(
            [result.ocr_text for result in results],
            [result.sample.text for result in results],
        ),
        "seconds_per_receipt": sum(seconds) / len(seconds),
        "fields_from_labelled_lines": fields_from_labels,
    }


def label_line_accuracy(
    config: dict,
    samples: list[Sample],
    doctype: DocumentType,
) -> dict:
    """Field accuracy when the extractor reads the labelled lines instead of OCR.

    Separates field-extraction errors from OCR errors.
    """

    extract = build_fields(config["fields"])

    return field_accuracy(
        [extract(sample.lines) for sample in samples],
        [sample.fields for sample in samples],
        doctype,
    )


def per_receipt_rows(results: list[SampleResult], doctype: DocumentType) -> list[dict]:
    rows = []

    for result in results:
        truth = result.sample.fields
        rows.append(
            {
                "id": result.sample.id,
                "matches": field_matches(result.prediction, truth, doctype),
                "predicted": doctype.normalize(result.prediction),
                "expected": doctype.normalize(truth),
                "seconds": round(result.seconds, 3),
            }
        )

    return rows


def render_markdown(summary: dict, metadata: dict) -> str:
    accuracy = summary["field_accuracy"]
    fields = [field for field in accuracy if field != "mean"]
    lines = [
        f"# {metadata['config']}: {metadata['description']}",
        "",
        *[f"- **{key}:** {value}" for key, value in metadata.items()],
        f"- **Receipts:** {summary['receipts']}",
        "",
        "## Field accuracy",
        "",
        "Normalized exact match per field (see `extractor/schema.py`).",
        "",
        "| Source | " + " | ".join(fields) + " | Mean |",
        "|---" * (len(fields) + 2) + "|",
        "| OCR | "
        + " | ".join(f"{accuracy[field]:.3f}" for field in fields)
        + f" | {accuracy['mean']:.3f} |",
    ]

    labelled = summary["fields_from_labelled_lines"]
    if labelled is not None:
        lines.append(
            "| Labelled lines | "
            + " | ".join(f"{labelled[field]:.3f}" for field in fields)
            + f" | {labelled['mean']:.3f} |"
        )
        lines += [
            "",
            "\"Labelled lines\" runs the same field extractor on the dataset's "
            "line transcripts instead of OCR output: the gap to the OCR row is "
            "what OCR errors cost.",
        ]

    lines += [
        "",
        "## OCR and speed",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| CER (corpus) | {summary['cer']:.3f} |",
        f"| Seconds per receipt (CPU) | {summary['seconds_per_receipt']:.2f} |",
        "",
        "CER compares OCR lines joined in the engine's reading order with the "
        "labelled lines joined in annotation order.",
        "",
    ]

    return "\n".join(lines)


def write_reports(
    output_dir: Path,
    summary: dict,
    metadata: dict,
    rows: list[dict],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "report.json").write_text(
        json.dumps({"metadata": metadata, **summary}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_markdown(summary, metadata),
        encoding="utf-8",
    )
    (output_dir / "receipts.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def cpu_name() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as file:
            for line in file:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass

    return platform.processor() or platform.machine()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.run",
        description="Run an experiment config on a SROIE split.",
    )
    parser.add_argument("--config", required=True, help="e1, or a path to a YAML file")
    parser.add_argument("--split", default="test", choices=SPLITS)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--data", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    doctype = load_doctype(config["doctype"])
    pipeline = build_pipeline(config)
    samples = load_sroie(args.data, split=args.split, limit=args.limit, doctype=doctype)

    results = run_samples(pipeline, samples)
    summary = summarize(results, doctype, label_line_accuracy(config, samples, doctype))

    metadata = {
        "config": config["name"],
        "description": config.get("description", ""),
        "doctype": doctype.name,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "split": args.split if args.limit is None else f"{args.split} (first {args.limit})",
        "ocr": f"{pipeline.ocr.name} {pipeline.ocr.version()}",
        "cpu": cpu_name(),
    }

    name = config["name"] if args.split == "test" else f"{config['name']}-{args.split}"
    if args.limit is not None:
        name += f"-first{args.limit}"

    output_dir = args.output / name
    write_reports(output_dir, summary, metadata, per_receipt_rows(results, doctype))
    print(render_markdown(summary, metadata))
    print(f"Reports written to {output_dir}")


if __name__ == "__main__":
    main()
