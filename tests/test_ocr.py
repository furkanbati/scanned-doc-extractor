import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from extractor.ocr.base import lines_to_text, rect_box
from extractor.ocr.tesseract import TesseractOcr, group_words


def tesseract_data(words):
    """Build pytesseract's image_to_data dict from (text, line, left) tuples."""

    data = {key: [] for key in (
        "text", "conf", "block_num", "par_num", "line_num",
        "left", "top", "width", "height",
    )}

    # Tesseract also emits a row per block/line with conf -1 and no text.
    rows = [("", -1, 1, 0, 0)] + [(text, 90, 1, line, left) for text, line, left in words]

    for text, conf, block, line, left in rows:
        data["text"].append(text)
        data["conf"].append(conf)
        data["block_num"].append(block)
        data["par_num"].append(1)
        data["line_num"].append(line)
        data["left"].append(left)
        data["top"].append(line * 20)
        data["width"].append(30)
        data["height"].append(10)

    return data


def test_rect_box():
    assert rect_box(1, 2, 3, 4) == (1, 2, 3, 2, 3, 4, 1, 4)


def test_group_words_into_lines():
    data = tesseract_data([
        ("ABC", 1, 10), ("SDN", 1, 50), ("BHD", 1, 90),
        ("TOTAL", 2, 10), ("9.00", 2, 200), ("  ", 2, 300),
    ])

    lines = group_words(data)

    assert [line.text for line in lines] == ["ABC SDN BHD", "TOTAL 9.00"]
    assert lines[0].box == rect_box(10, 20, 120, 30)
    assert lines_to_text(lines) == "ABC SDN BHD\nTOTAL 9.00"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_tesseract_reads_rendered_text():
    image = Image.new("RGB", (900, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 60), "TOTAL 9.00", fill="black", font=ImageFont.load_default(size=64))

    lines = TesseractOcr().read(image)

    assert lines_to_text(lines).replace(" ", "") == "TOTAL9.00"
