from decimal import Decimal

import pytest
from PIL import Image

from extractor.fields.base import Extraction
from extractor.ocr.base import OcrLine, rect_box
from extractor.ocr.rapid import group_into_lines
from extractor.pipeline import Pipeline, build_pipeline, load_engine, reads_line_items
from extractor.schema import load_doctype

RECEIPT = load_doctype("receipt")


def segment(text, left, top, right, bottom):
    return OcrLine(text=text, box=rect_box(left, top, right, bottom))


SEGMENTS = [
    segment("NASI GORENG", 50, 100, 200, 120),
    segment("25,000", 540, 100, 600, 120),
    segment("ES TEH", 50, 130, 120, 150),
    segment("5,000", 550, 130, 600, 150),
    segment("TOTAL", 50, 170, 110, 190),
    segment("30,000", 540, 170, 600, 190),
]


class SegmentOcr:
    """Like RapidOcr: boxes as detected, joined into lines on request."""

    name = "fake"

    def __init__(self):
        self.calls = 0

    def read_segments(self, image):
        self.calls += 1
        return SEGMENTS

    def lines(self, segments):
        return group_into_lines(segments)

    def read(self, image):
        return self.lines(self.read_segments(image))


class LineOcr:
    """An OCR engine that only returns lines (Tesseract, say)."""

    name = "lines"

    def read(self, image):
        return group_into_lines(SEGMENTS)


class RecordingExtractor:
    name = "recording"
    needs_ocr = True

    def __init__(self):
        self.seen = None

    def extract(self, image, lines):
        self.seen = lines
        return Extraction(fields={"total": "30,000"})


IMAGE = Image.new("RGB", (10, 10))


def test_line_items_read_ocr_once_and_leave_the_field_input_unchanged():
    ocr, extractor = SegmentOcr(), RecordingExtractor()

    result = Pipeline(ocr, extractor, line_items=True).run(IMAGE)

    assert ocr.calls == 1
    # The field extractor gets the same joined lines as without line items.
    assert extractor.seen == group_into_lines(SEGMENTS)
    assert result.lines == group_into_lines(SEGMENTS)
    assert result.line_items.amounts == [Decimal(25000), Decimal(5000)]
    assert result.line_items.total == Decimal(30000)
    assert not result.line_items.needs_review


def test_without_line_items_nothing_changes():
    ocr, extractor = SegmentOcr(), RecordingExtractor()

    result = Pipeline(ocr, extractor).run(IMAGE)

    assert result.line_items is None
    assert extractor.seen == group_into_lines(SEGMENTS)


def test_an_ocr_engine_without_segments_still_gives_line_items_from_its_lines():
    result = Pipeline(LineOcr(), RecordingExtractor(), line_items=True).run(IMAGE)

    assert result.line_items is not None


def test_line_items_need_ocr():
    class Vision:
        name = "vision"
        needs_ocr = False

    with pytest.raises(ValueError):
        Pipeline(None, Vision(), line_items=True)


def test_only_e2_reads_line_items():
    assert reads_line_items(load_engine("e2"), RECEIPT)
    for name in ("e1", "e3", "e4", "e5"):
        assert not reads_line_items(load_engine(name), RECEIPT)


def test_build_pipeline_turns_line_items_on_for_e2(monkeypatch):
    import extractor.pipeline as pipeline

    monkeypatch.setattr(pipeline, "build_ocr", lambda name, options=None: SegmentOcr())

    assert build_pipeline(load_engine("e2"), RECEIPT).line_items is True
    assert build_pipeline(load_engine("e1"), RECEIPT).line_items is False


def test_a_document_type_without_line_items_turns_them_off(monkeypatch):
    import extractor.pipeline as pipeline

    monkeypatch.setattr(pipeline, "build_ocr", lambda name, options=None: SegmentOcr())
    plain = RECEIPT.model_copy(update={"line_items": False})

    assert build_pipeline(load_engine("e2"), plain).line_items is False


def test_unknown_line_item_extractor():
    config = {**load_engine("e2"), "line_items": "magic"}

    with pytest.raises(ValueError):
        build_pipeline(config, RECEIPT)
