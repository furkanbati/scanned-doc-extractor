from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from extractor.dataset import OcrLine
from extractor.ocr.base import OcrEngine
from extractor.schema import Fields


@dataclass(frozen=True)
class PipelineResult:
    fields: Fields
    lines: list[OcrLine]


class Pipeline:
    """image -> OCR lines -> fields. Preprocessing joins on day 3."""

    def __init__(
        self,
        ocr: OcrEngine,
        fields: Callable[[list[OcrLine]], Fields],
    ):
        self.ocr = ocr
        self.fields = fields

    def run(self, image: Image.Image) -> PipelineResult:
        lines = self.ocr.read(image)

        return PipelineResult(fields=self.fields(lines), lines=lines)


def build_ocr(name: str, options: dict | None = None) -> OcrEngine:
    if name == "tesseract":
        from extractor.ocr.tesseract import TesseractOcr

        return TesseractOcr(**(options or {}))

    if name == "rapidocr":
        from extractor.ocr.rapid import RapidOcr

        return RapidOcr(**(options or {}))

    raise ValueError(f"Unknown OCR engine {name!r}")


def build_fields(name: str) -> Callable[[list[OcrLine]], Fields]:
    if name == "rules":
        from extractor.fields.rules import extract_fields

        return extract_fields

    raise ValueError(f"Unknown field extractor {name!r}")


def build_pipeline(config: dict) -> Pipeline:
    preprocess = config.get("preprocess", "none")
    if preprocess != "none":
        raise ValueError(f"Unknown preprocessing {preprocess!r}")

    return Pipeline(
        ocr=build_ocr(config["ocr"], config.get("ocr_options")),
        fields=build_fields(config["fields"]),
    )
