"""Fields read with a user's rule set (extractor/templates), from OCR lines."""

from extractor.fields.base import Extraction
from extractor.ocr.base import OcrLine
from extractor.schema import DocumentType
from extractor.templates.read import extract


class TemplateExtractor:
    name = "template"
    needs_ocr = True

    def __init__(self, template: dict, doctype: DocumentType):
        self.template = template
        self.doctype = doctype

    def extract(self, image, lines: list[OcrLine]) -> Extraction:
        return Extraction(fields=extract(self.template, self.doctype, lines))
