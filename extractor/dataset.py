"""Loads SROIE receipts downloaded by scripts/download_sroie.sh.

The corrected data/ folder of zzzDavid/ICDAR-2019-SROIE holds the 626
labelled trainval receipts; the official test labels are not part of it.
We split those 626 receipts once, with a fixed seed: 400 for the reported
results ("test") and 226 for tuning rules and prompts ("dev").
"""

import json
import random
from dataclasses import dataclass
from pathlib import Path

from extractor.schema import DocumentType, Fields, load_doctype

DEFAULT_ROOT = Path("data/sroie")
SPLIT_SEED = 2019
TEST_SIZE = 400
SPLITS = ("test", "dev", "all")


@dataclass(frozen=True)
class OcrLine:
    text: str
    box: tuple[int, ...]  # x1, y1, ..., x4, y4, clockwise from top left


@dataclass(frozen=True)
class Sample:
    id: str
    image_path: Path
    fields: Fields
    lines: list[OcrLine]

    @property
    def text(self) -> str:
        """Labelled line transcripts, the reference for CER."""

        return "\n".join(line.text for line in self.lines)


def parse_box_line(raw: str) -> OcrLine | None:
    parts = raw.rstrip("\r\n").split(",", 8)
    if len(parts) < 9:
        return None

    return OcrLine(text=parts[8], box=tuple(int(value) for value in parts[:8]))


def load_lines(path: Path) -> list[OcrLine]:
    with path.open(encoding="utf-8", errors="replace") as file:
        return [line for raw in file if (line := parse_box_line(raw))]


def load_fields(path: Path, doctype: DocumentType) -> Fields:
    with path.open(encoding="utf-8") as file:
        return doctype.values(json.load(file))


def split_ids(ids: list[str], split: str) -> list[str]:
    if split not in SPLITS:
        raise ValueError(f"Unknown split {split!r}, expected one of {SPLITS}")

    if split == "all":
        return sorted(ids)

    shuffled = sorted(ids)
    random.Random(SPLIT_SEED).shuffle(shuffled)
    chosen = shuffled[:TEST_SIZE] if split == "test" else shuffled[TEST_SIZE:]

    return sorted(chosen)


def load_sroie(
    root: Path = DEFAULT_ROOT,
    split: str = "test",
    limit: int | None = None,
    doctype: DocumentType | None = None,
) -> list[Sample]:
    doctype = doctype or load_doctype("receipt")
    root = Path(root)
    image_dir = root / "img"

    if not image_dir.is_dir():
        raise FileNotFoundError(
            f"{image_dir} not found; run: bash scripts/download_sroie.sh"
        )

    ids = split_ids([path.stem for path in image_dir.glob("*.jpg")], split)
    if limit is not None:
        ids = ids[:limit]

    return [
        Sample(
            id=sample_id,
            image_path=image_dir / f"{sample_id}.jpg",
            fields=load_fields(root / "key" / f"{sample_id}.json", doctype),
            lines=load_lines(root / "box" / f"{sample_id}.csv"),
        )
        for sample_id in ids
    ]


if __name__ == "__main__":
    doctype = load_doctype("receipt")

    for name in SPLITS:
        samples = load_sroie(split=name, doctype=doctype)
        missing = {
            field: sum(doctype.normalize(s.fields)[field] is None for s in samples)
            for field in doctype.field_names
        }
        print(
            f"{name}: {len(samples)} receipts, "
            f"{sum(len(s.lines) for s in samples)} labelled lines, "
            f"missing fields {missing}"
        )
