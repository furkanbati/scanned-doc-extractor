from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from PIL import Image

from extractor.ocr.base import OcrLine
from extractor.schema import Fields

if TYPE_CHECKING:
    from extractor.fields.line_items import LineItemResult


@dataclass(frozen=True)
class Usage:
    """Tokens a model call consumed; output includes reasoning tokens."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Extraction:
    fields: Fields
    usage: Usage | None = None
    # Set by an extractor that reads line items itself (a model asked for them).
    line_items: "LineItemResult | None" = None


class FieldExtractor(Protocol):
    """Finds the document type's fields in an image and/or its OCR lines."""

    name: str
    # False: the extractor reads the image itself and the pipeline skips OCR.
    needs_ocr: bool

    def extract(self, image: Image.Image, lines: list[OcrLine]) -> Extraction: ...
