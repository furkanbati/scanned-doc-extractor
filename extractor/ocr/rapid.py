"""RapidOCR: PaddleOCR's detection and recognition networks run with ONNX Runtime."""

from importlib.metadata import version as package_version

import numpy as np
from PIL import Image

from extractor.ocr.base import OcrLine
from extractor.ocr.base import rect_box


class RapidOcr:
    name = "rapidocr"

    def __init__(self, group_lines: bool = False, log_level: str = "warning"):
        from rapidocr import RapidOCR

        self.group_lines = group_lines
        self.engine = RapidOCR(params={"Global.log_level": log_level})

    def read(self, image: Image.Image) -> list[OcrLine]:
        output = self.engine(np.asarray(image.convert("RGB"))[:, :, ::-1])
        if output.boxes is None:
            return []

        segments = [
            OcrLine(text=text, box=tuple(int(round(v)) for point in box for v in point))
            for box, text in zip(output.boxes, output.txts)
            if text.strip()
        ]

        return group_into_lines(segments) if self.group_lines else segments

    def version(self) -> str:
        return f"{package_version('rapidocr')} (PP-OCRv6 small)"


def _vertical_span(line: OcrLine) -> tuple[int, int]:
    ys = line.box[1::2]

    return min(ys), max(ys)


def group_into_lines(segments: list[OcrLine], overlap: float = 0.5) -> list[OcrLine]:
    """Join text boxes that sit on the same printed line, left to right.

    Two boxes share a line when their vertical spans overlap by at least
    `overlap` of the shorter box's height.
    """

    rows: list[list[OcrLine]] = []

    for segment in sorted(segments, key=lambda line: sum(_vertical_span(line)) / 2):
        top, bottom = _vertical_span(segment)

        for row in rows:
            row_top, row_bottom = _vertical_span(row[-1])
            shared = min(bottom, row_bottom) - max(top, row_top)
            if shared >= overlap * min(bottom - top, row_bottom - row_top):
                row.append(segment)
                break
        else:
            rows.append([segment])

    lines = []
    for row in rows:
        row.sort(key=lambda line: min(line.box[0::2]))
        xs = [x for line in row for x in line.box[0::2]]
        ys = [y for line in row for y in line.box[1::2]]
        lines.append(
            OcrLine(
                text=" ".join(line.text for line in row),
                box=rect_box(min(xs), min(ys), max(xs), max(ys)),
            )
        )

    return lines
