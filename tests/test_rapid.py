import importlib.util

import pytest
from PIL import Image, ImageDraw, ImageFont

from extractor.ocr.base import OcrLine
from extractor.ocr.base import lines_to_text, rect_box
from extractor.ocr.rapid import group_into_lines


def segment(text, left, top, right, bottom):
    return OcrLine(text=text, box=rect_box(left, top, right, bottom))


def test_group_joins_boxes_on_the_same_line():
    segments = [
        segment("33.90", 300, 102, 360, 122),
        segment("TOTAL ROUNDED", 20, 100, 200, 120),
        segment("MR DIY SDN BHD", 40, 10, 300, 30),
    ]

    lines = group_into_lines(segments)

    assert [line.text for line in lines] == ["MR DIY SDN BHD", "TOTAL ROUNDED 33.90"]
    assert lines[1].box == rect_box(20, 100, 360, 122)


def test_group_keeps_stacked_boxes_apart():
    segments = [
        segment("TOTAL", 20, 100, 100, 120),
        segment("CASH", 20, 125, 100, 145),
    ]

    assert [line.text for line in group_into_lines(segments)] == ["TOTAL", "CASH"]


@pytest.mark.skipif(importlib.util.find_spec("rapidocr") is None, reason="rapidocr not installed")
def test_rapidocr_reads_rendered_text():
    from extractor.ocr.rapid import RapidOcr

    image = Image.new("RGB", (900, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 60), "TOTAL 9.00", fill="black", font=ImageFont.load_default(size=64))

    lines = RapidOcr(group_lines=True).read(image)

    assert lines_to_text(lines).replace(" ", "") == "TOTAL9.00"
