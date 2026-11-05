from dataclasses import dataclass
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class OcrLine:
    text: str
    box: tuple[int, ...]  # x1, y1, ..., x4, y4, clockwise from top left


class OcrEngine(Protocol):
    """Reads an image into text lines with their boxes, in reading order."""

    name: str

    def read(self, image: Image.Image) -> list[OcrLine]: ...

    def version(self) -> str: ...


def rect_box(left: int, top: int, right: int, bottom: int) -> tuple[int, ...]:
    """Axis-aligned rectangle as the 8-value SROIE box, clockwise from top left."""

    return (left, top, right, top, right, bottom, left, bottom)


def lines_to_text(lines: list[OcrLine]) -> str:
    return "\n".join(line.text for line in lines)
