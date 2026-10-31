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
from extractor.fields.base import Usage
from extractor.pipeline import Pipeline, build_extractor, build_pipeline
from extractor.schema import DocumentType, Fields, load_doctype

CONFIG_DIR = Path(__file__).parent / "configs"
DEFAULT_OUTPUT = Path(__file__).parent / "results"


@dataclass(frozen=True)
class SampleResult:
    sample: Sample
    prediction: Fields
    ocr_text: str
    seconds: float
    usage: Usage | None = None
    error: str | None = None


def load_config(name: str) -> dict:
    path = Path(name)
    if not path.is_file():
        path = CONFIG_DIR / f"{name}.yaml"

    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def result_to_json(result: SampleResult) -> dict:
    return {
        "id": result.sample.id,
        "prediction": result.prediction,
        "ocr_text": result.ocr_text,
        "seconds": result.seconds,
        "usage": vars(result.usage) if result.usage else None,
        "error": result.error,
    }


def result_from_json(data: dict, sample: Sample) -> SampleResult:
    return SampleResult(
        sample=sample,
        prediction=data["prediction"],
        ocr_text=data["ocr_text"],
        seconds=data["seconds"],
        usage=Usage(**data["usage"]) if data["usage"] else None,
        error=data["error"],
    )


def load_checkpoint(path: Path | None, samples: list[Sample]) -> dict[str, SampleResult]:
    if path is None or not path.is_file():
        return {}

    by_id = {sample.id: sample for sample in samples}
    done = {}

    with path.open(encoding="utf-8") as file:
        for line in file:
            data = json.loads(line)
            if data["id"] in by_id:
                done[data["id"]] = result_from_json(data, by_id[data["id"]])

    return done


def run_samples(
    pipeline: Pipeline,
    samples: list[Sample],
    checkpoint: Path | None = None,
) -> list[SampleResult]:
    """Run every sample. A sample that fails (a model call that keeps failing,
    say) counts as an empty prediction and keeps its error; the run goes on.

    With a checkpoint file, each result is appended to it as soon as it is
    done, and samples already in it are not run again: a run that crashed
    continues where it stopped.
    """

    done = load_checkpoint(checkpoint, samples)
    if done:
        print(f"Resuming: {len(done)} receipts already in {checkpoint}", flush=True)

    results = []

    for number, sample in enumerate(samples, start=1):
        if sample.id in done:
            results.append(done[sample.id])
            continue

        with Image.open(sample.image_path) as image:
            image.load()
            start = time.perf_counter()
            try:
                output = pipeline.run(image)
            except Exception as error:
                output, failure = None, f"{type(error).__name__}: {error}"
                print(f"{sample.id} failed: {failure}", flush=True)
            seconds = time.perf_counter() - start

        if output is None:
            results.append(
                SampleResult(
                    sample=sample,
                    prediction={},
                    ocr_text="",
                    seconds=seconds,
                    error=failure,
                )
            )
        else:
            results.append(
                SampleResult(
                    sample=sample,
                    prediction=output.fields,
                    ocr_text=lines_to_text(output.lines),
                    seconds=seconds,
                    usage=output.usage,
                )
            )

        if checkpoint is not None:
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            with checkpoint.open("a", encoding="utf-8") as file:
                file.write(json.dumps(result_to_json(results[-1]), ensure_ascii=False) + "\n")

        if number % 25 == 0 or number == len(samples):
            print(f"{number}/{len(samples)} receipts", flush=True)

    return results


def summarize(
    results: list[SampleResult],
    doctype: DocumentType,
    fields_from_labels: dict | None,
    has_ocr: bool = True,
    prices: dict | None = None,
) -> dict:
    """Accuracy, CER (OCR pipelines only), speed and, for model calls, tokens and cost.

    prices: USD per million input and output tokens.
    """

    truths = [result.sample.fields for result in results]
    seconds = [result.seconds for result in results]
    usages = [result.usage for result in results if result.usage is not None]

    summary = {
        "receipts": len(results),
        "failed": sum(result.error is not None for result in results),
        "field_accuracy": field_accuracy(
            [result.prediction for result in results], truths, doctype
        ),
        "cer": corpus_cer(
            [result.ocr_text for result in results],
            [result.sample.text for result in results],
        ) if has_ocr else None,
        "seconds_per_receipt": sum(seconds) / len(seconds),
        "fields_from_labelled_lines": fields_from_labels,
        "tokens": None,
        "usd_per_receipt": None,
    }

    if usages:
        tokens = {
            "input": sum(usage.input_tokens for usage in usages),
            "output": sum(usage.output_tokens for usage in usages),
        }
        summary["tokens"] = tokens

        if prices:
            cost = (tokens["input"] * prices["input"] + tokens["output"] * prices["output"]) / 1e6
            summary["usd_per_receipt"] = cost / len(results)

    return summary


def label_line_accuracy(
    config: dict,
    samples: list[Sample],
    doctype: DocumentType,
) -> dict | None:
    """Field accuracy when the extractor reads the labelled lines instead of OCR.

    Separates field-extraction errors from OCR errors. Only for extractors that
    read text without a model call (re-running a paid model would double the cost).
    """

    if config["fields"] != "rules":
        return None

    extractor = build_extractor(config["fields"], doctype, config.get("fields_options"))

    return field_accuracy(
        [extractor.extract(None, sample.lines).fields for sample in samples],
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
                "error": result.error,
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
        f"- **Receipts:** {summary['receipts']} ({summary['failed']} failed, counted as empty)",
        "",
        "## Field accuracy",
        "",
        "Normalized exact match per field (see `extractor/schema.py`).",
        "",
        "| Source | " + " | ".join(fields) + " | Mean |",
        "|---" * (len(fields) + 2) + "|",
        "| Pipeline | "
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
            "line transcripts instead of OCR output: the gap to the Pipeline row is "
            "what OCR errors cost.",
        ]

    lines += [
        "",
        "## OCR, speed and cost",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]

    if summary["cer"] is not None:
        lines.append(f"| CER (corpus) | {summary['cer']:.3f} |")

    lines.append(f"| Seconds per receipt | {summary['seconds_per_receipt']:.2f} |")

    if summary["tokens"] is not None:
        receipts = summary["receipts"]
        lines += [
            f"| Input tokens per receipt | {summary['tokens']['input'] / receipts:.0f} |",
            "| Output tokens per receipt (incl. reasoning) | "
            f"{summary['tokens']['output'] / receipts:.0f} |",
        ]

    if summary["usd_per_receipt"] is not None:
        lines += [
            f"| USD per receipt | {summary['usd_per_receipt']:.5f} |",
            f"| USD per 1,000 receipts | {summary['usd_per_receipt'] * 1000:.2f} |",
        ]

    if summary["cer"] is not None:
        lines += [
            "",
            "CER compares OCR lines joined in the engine's reading order with the "
            "labelled lines joined in annotation order.",
        ]

    lines.append("")

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
    pipeline = build_pipeline(config, doctype)
    samples = load_sroie(args.data, split=args.split, limit=args.limit, doctype=doctype)

    name = config["name"] if args.split == "test" else f"{config['name']}-{args.split}"
    if args.limit is not None:
        name += f"-first{args.limit}"

    output_dir = args.output / name
    checkpoint = output_dir / "checkpoint.jsonl"

    results = run_samples(pipeline, samples, checkpoint)
    summary = summarize(
        results,
        doctype,
        label_line_accuracy(config, samples, doctype),
        has_ocr=pipeline.ocr is not None,
        prices=config.get("price_per_million_tokens"),
    )

    metadata = {
        "config": config["name"],
        "description": config.get("description", ""),
        "doctype": doctype.name,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "split": args.split if args.limit is None else f"{args.split} (first {args.limit})",
        "ocr": f"{pipeline.ocr.name} {pipeline.ocr.version()}" if pipeline.ocr else "none",
        "fields": pipeline.extractor.name
        + (f" ({pipeline.extractor.model})" if hasattr(pipeline.extractor, "model") else ""),
        "cpu": cpu_name(),
    }

    write_reports(output_dir, summary, metadata, per_receipt_rows(results, doctype))
    checkpoint.unlink(missing_ok=True)
    print(render_markdown(summary, metadata))
    print(f"Reports written to {output_dir}")


if __name__ == "__main__":
    main()
