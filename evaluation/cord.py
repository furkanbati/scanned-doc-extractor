"""Loads CORD v2 receipts downloaded by scripts/download_cord.sh.

CORD (Park et al., 2019, CC-BY-4.0) holds 1,000 Indonesian receipts split by
its authors into train (800), validation (100) and test (100). Each receipt's
`ground_truth` JSON carries:

- `gt_parse`: the parsed receipt. `menu` is one item (a dict) or a list of
  items with `nm`, `cnt`, `price` (the line amount), `unitprice`, `sub`
  (modifiers such as "+Hot") and more; `sub_total` holds `subtotal_price`,
  `tax_price`, `service_price`, `discount_price`; `total` holds `total_price`,
  `cashprice`, `changeprice` and others. A value is a string, or a list of
  strings when the receipt prints it more than once (then the last, lowest
  one is kept: "55,834" then the rounded "55,800").
- `valid_line`: the annotated words with their quads, grouped by field. Only
  the menu, sub_total and total words are annotated; the store name, address
  and footer are not in it.
"""

import io
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from PIL import Image

from extractor.amounts import parse_amount
from extractor.ocr.base import OcrLine

DEFAULT_ROOT = Path("data/cord")
SPLITS = ("train", "validation", "test")


@dataclass(frozen=True)
class TruthItem:
    name: str | None
    count: str | None
    amount: Decimal | None


@dataclass(frozen=True)
class CordTruth:
    """The values the line-item benchmark checks, normalized with parse_amount."""

    items: list[TruthItem]
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    service: Decimal | None = None
    discount: Decimal | None = None
    total: Decimal | None = None

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def amounts(self) -> list[Decimal | None]:
        return [item.amount for item in self.items]


@dataclass
class CordSample:
    id: str
    truth: CordTruth
    words: list[OcrLine]
    image_bytes: bytes | None = field(default=None, repr=False)

    def image(self) -> Image.Image:
        if self.image_bytes is None:
            raise ValueError(f"{self.id} was loaded without its image")

        image = Image.open(io.BytesIO(self.image_bytes))
        image.load()

        return image


def _last(value) -> str | None:
    """A label value; for a value printed several times, the last one."""

    if isinstance(value, list):
        strings = [item for item in value if isinstance(item, str)]
        return strings[-1] if strings else None

    return value if isinstance(value, str) else None


def _merge(section) -> dict:
    """sub_total / total as one dict (one receipt has a list of them)."""

    if isinstance(section, list):
        merged = {}
        for part in section:
            if isinstance(part, dict):
                merged.update(part)
        return merged

    return section if isinstance(section, dict) else {}


def parse_truth(gt_parse: dict) -> CordTruth:
    menu = gt_parse.get("menu") or []
    menu = menu if isinstance(menu, list) else [menu]
    sub_total = _merge(gt_parse.get("sub_total"))
    total = _merge(gt_parse.get("total"))

    def amount(section: dict, key: str) -> Decimal | None:
        return parse_amount(_last(section.get(key)))

    return CordTruth(
        items=[
            TruthItem(
                name=_last(item.get("nm")),
                count=_last(item.get("cnt")),
                amount=parse_amount(_last(item.get("price"))),
            )
            for item in menu
            if isinstance(item, dict)
        ],
        subtotal=amount(sub_total, "subtotal_price"),
        tax=amount(sub_total, "tax_price"),
        service=amount(sub_total, "service_price"),
        discount=amount(sub_total, "discount_price"),
        total=amount(total, "total_price"),
    )


def quad_box(quad: dict) -> tuple[int, ...]:
    """CORD quad (x1,y1 top left, clockwise) as the 8-value box of OcrLine."""

    return tuple(int(quad[f"{axis}{corner}"]) for corner in range(1, 5) for axis in "xy")


def parse_words(valid_line: list[dict]) -> list[OcrLine]:
    """The annotated words with their boxes, nothing else.

    The field category, group and row ids are left out: they are labels, and
    the oracle mode must find rows and fields the way it would from OCR.
    """

    return [
        OcrLine(text=word["text"], box=quad_box(word["quad"]))
        for line in valid_line
        for word in line["words"]
        if word["text"].strip()
    ]


def split_files(root: Path, split: str) -> list[Path]:
    if split not in SPLITS:
        raise ValueError(f"Unknown split {split!r}, expected one of {SPLITS}")

    files = sorted(Path(root).glob(f"{split}-*.parquet"))
    if not files:
        raise FileNotFoundError(f"No {split} files in {root}; run: bash scripts/download_cord.sh")

    return files


def load_cord(
    root: Path = DEFAULT_ROOT,
    split: str = "validation",
    limit: int | None = None,
    images: bool = False,
) -> Iterator[CordSample]:
    """The receipts of one split, in file order. Images are read only when asked for."""

    import pyarrow.parquet as pq

    columns = ["ground_truth", "image"] if images else ["ground_truth"]
    count = 0

    for path in split_files(root, split):
        parquet = pq.ParquetFile(path)

        for group in range(parquet.num_row_groups):
            table = parquet.read_row_group(group, columns=columns)
            truths = table.column("ground_truth").to_pylist()
            pictures = table.column("image").to_pylist() if images else [None] * len(truths)

            for raw, picture in zip(truths, pictures):
                if limit is not None and count >= limit:
                    return

                data = json.loads(raw)
                yield CordSample(
                    id=f"{split}-{data['meta']['image_id']:04d}",
                    truth=parse_truth(data["gt_parse"]),
                    words=parse_words(data["valid_line"]),
                    image_bytes=picture["bytes"] if picture else None,
                )
                count += 1


if __name__ == "__main__":
    for name in SPLITS:
        samples = list(load_cord(split=name))
        print(
            f"{name}: {len(samples)} receipts, "
            f"{sum(s.truth.item_count for s in samples)} items, "
            f"{sum(s.truth.total is None for s in samples)} without a total, "
            f"{sum(s.truth.subtotal is not None for s in samples)} with a subtotal"
        )
