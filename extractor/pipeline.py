from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image

from extractor.ocr.base import OcrLine
from extractor.fields.base import FieldExtractor, Usage
from extractor.ocr.base import OcrEngine
from extractor.schema import DocumentType, Fields

# One YAML file per engine: OCR, field extractor, model, prices, measured results.
ENGINE_DIR = Path(__file__).parent / "engines"


def engine_names() -> list[str]:
    return sorted(path.stem for path in ENGINE_DIR.glob("*.yaml"))


def load_engine(name: str | Path) -> dict:
    """An engine by name (extractor/engines/<name>.yaml) or by file path."""

    path = Path(name)
    if not path.is_file():
        path = ENGINE_DIR / f"{name}.yaml"

    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


@dataclass(frozen=True)
class PipelineResult:
    fields: Fields
    lines: list[OcrLine]
    usage: Usage | None = None


class Pipeline:
    """image -> (OCR lines) -> fields.

    OCR runs only when the field extractor needs it; a vision model reads the
    image itself.
    """

    def __init__(self, ocr: OcrEngine | None, extractor: FieldExtractor):
        if extractor.needs_ocr and ocr is None:
            raise ValueError(f"{extractor.name} needs an OCR engine")

        self.ocr = ocr
        self.extractor = extractor

    def run(self, image: Image.Image) -> PipelineResult:
        lines = self.ocr.read(image) if self.ocr is not None else []
        extraction = self.extractor.extract(image, lines)

        return PipelineResult(fields=extraction.fields, lines=lines, usage=extraction.usage)


def build_ocr(name: str, options: dict | None = None) -> OcrEngine | None:
    if name == "none":
        return None

    if name == "tesseract":
        from extractor.ocr.tesseract import TesseractOcr

        return TesseractOcr(**(options or {}))

    if name == "rapidocr":
        from extractor.ocr.rapid import RapidOcr

        return RapidOcr(**(options or {}))

    raise ValueError(f"Unknown OCR engine {name!r}")


def build_extractor(
    name: str,
    doctype: DocumentType,
    options: dict | None = None,
) -> FieldExtractor:
    if name == "rules":
        from extractor.fields.rules import RuleExtractor

        return RuleExtractor(**(options or {}))

    if name == "openai_vision":
        from extractor.fields.openai_vision import OpenAIVisionExtractor

        return OpenAIVisionExtractor(doctype, **(options or {}))

    if name == "local_llm":
        from extractor.fields.local_llm import LocalLLMExtractor

        return LocalLLMExtractor(doctype, **(options or {}))

    if name == "local_vision":
        from extractor.fields.local_vision import LocalVisionExtractor

        return LocalVisionExtractor(doctype, **(options or {}))

    raise ValueError(f"Unknown field extractor {name!r}")


def build_pipeline(config: dict, doctype: DocumentType) -> Pipeline:
    preprocess = config.get("preprocess", "none")
    if preprocess != "none":
        raise ValueError(f"Unknown preprocessing {preprocess!r}")

    return Pipeline(
        ocr=build_ocr(config["ocr"], config.get("ocr_options")),
        extractor=build_extractor(config["fields"], doctype, config.get("fields_options")),
    )
