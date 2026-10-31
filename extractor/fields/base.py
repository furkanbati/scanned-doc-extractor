from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from extractor.dataset import OcrLine
from extractor.schema import Fields


@dataclass(frozen=True)
class Usage:
    """Tokens a model call consumed; output includes reasoning tokens."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Extraction:
    fields: Fields
    usage: Usage | None = None


class FieldExtractor(Protocol):
    """Finds the document type's fields in an image and/or its OCR lines."""

    name: str
    # False: the extractor reads the image itself and the pipeline skips OCR.
    needs_ocr: bool

    def extract(self, image: Image.Image, lines: list[OcrLine]) -> Extraction: ...
