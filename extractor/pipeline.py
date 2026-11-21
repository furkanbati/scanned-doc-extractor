from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image

from extractor.ocr.base import OcrLine
from extractor.fields.base import FieldExtractor, Usage
from extractor.fields.line_items import LineItemResult, extract_line_items
from extractor.ocr.base import OcrEngine
from extractor.ocr.pdf_text import TextLayer
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
    # None when the engine does not read line items.
    line_items: LineItemResult | None = None


class Pipeline:
    """image -> (OCR lines) -> fields, and line items when asked for.

    OCR runs only when the field extractor needs it; a vision model reads the
    image itself. With line items, OCR still runs once: the line-item rules
    get its boxes as detected, the field extractor the same boxes joined into
    lines, exactly what it would get without line items.
    """

    def __init__(self, ocr: OcrEngine | None, extractor: FieldExtractor, line_items: bool = False):
        if extractor.needs_ocr and ocr is None:
            raise ValueError(f"{extractor.name} needs an OCR engine")
        if line_items and ocr is None:
            raise ValueError("line items need an OCR engine")

        self.ocr = ocr
        self.extractor = extractor
        self.line_items = line_items

    def run(self, image: Image.Image, text: "TextLayer | None" = None) -> PipelineResult:
        """`text`: a digital PDF's text layer, read in place of OCR."""

        segments = None
        if self.ocr is None:
            lines = []
        elif text is not None:
            segments, lines = text.segments, text.lines
        elif self.line_items and hasattr(self.ocr, "read_segments"):
            segments = self.ocr.read_segments(image)
            lines = self.ocr.lines(segments)
        else:
            lines = self.ocr.read(image)

        extraction = self.extractor.extract(image, lines)
        if extraction.line_items is not None:
            # The extractor (a model) read them itself.
            items = extraction.line_items
        elif self.line_items:
            items = extract_line_items(segments if segments is not None else lines)
        else:
            items = None

        return PipelineResult(fields=extraction.fields, lines=lines, usage=extraction.usage, line_items=items)


# Field extractors that can be asked for line items (line_items: model).
MODEL_LINE_ITEMS = {"local_vision", "openai_vision"}


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

    line_items = config.get("line_items", "none")
    if line_items not in ("none", "rules", "model"):
        raise ValueError(f"Unknown line-item extractor {line_items!r}")
    if line_items == "model" and config["fields"] not in MODEL_LINE_ITEMS:
        raise ValueError(f"{config['fields']} cannot read line items")

    # Read only when the document type has them and the engine can read them.
    wanted = doctype.line_items and line_items != "none"
    options = dict(config.get("fields_options") or {})
    if wanted and line_items == "model":
        options["line_items"] = True

    return Pipeline(
        ocr=build_ocr(config["ocr"], config.get("ocr_options")),
        extractor=build_extractor(config["fields"], doctype, options),
        # "rules": from the OCR boxes; "model": the extractor returns them.
        line_items=wanted and line_items == "rules",
    )


def build_template_pipeline(template: dict, doctype: DocumentType) -> Pipeline:
    """A user's rule set on E2's OCR, with E2's line-item rules."""

    from extractor.fields.template import TemplateExtractor

    config = load_engine("e2")

    return Pipeline(
        ocr=build_ocr(config["ocr"], config.get("ocr_options")),
        extractor=TemplateExtractor(template, doctype),
        line_items=reads_line_items(config, doctype),
    )


def reads_line_items(config: dict, doctype: DocumentType) -> bool:
    return doctype.line_items and config.get("line_items", "none") != "none"
