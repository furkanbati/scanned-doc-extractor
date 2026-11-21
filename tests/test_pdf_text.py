"""A digital PDF's text layer read as OCR lines. The PDFs are made up."""

import io

from PIL import Image

from extractor.ocr.pdf_text import pdf_text_lines, rows
from tests.pdf_maker import text_pdf

INVOICE = [(50, 50, 14, "ABC GIDA SANAYI"), (50, 80, 10, "Fatura No: ABC2024000153"),
           (50, 100, 10, "Un 50 kg"), (400, 100, 10, "1.250,00"), (50, 130, 10, "Genel Toplam 1.250,00 TL")]


def test_the_rows_of_a_text_pdf_are_its_lines():
    lines = pdf_text_lines(text_pdf(INVOICE), dpi=200)

    assert [line.text for line in lines] == ["ABC GIDA SANAYI", "Fatura No: ABC2024000153",
                                             "Un 50 kg 1.250,00", "Genel Toplam 1.250,00 TL"]


def test_boxes_are_in_the_rendered_page_s_pixels():
    lines = pdf_text_lines(text_pdf(INVOICE), dpi=200)

    left, top, right, _, _, _, _, bottom = lines[0].box
    # 50 pt from the left and the top at 200 dpi is about 139 px.
    assert 130 <= left <= 145 and 130 <= top <= 150
    assert top < bottom and left < right
    # The row with two pieces spans both.
    assert lines[2].box[2] > 400 * 200 / 72


def test_a_page_with_little_text_is_left_to_ocr():
    assert pdf_text_lines(text_pdf([(50, 50, 10, "Sayfa 1")]), dpi=200) is None


def test_a_scan_in_a_pdf_has_no_text_layer():
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buffer, format="PDF")

    assert pdf_text_lines(buffer.getvalue(), dpi=200) is None


def test_not_a_pdf_is_none():
    assert pdf_text_lines(b"not a pdf", dpi=200) is None


def test_pieces_on_one_row_join_left_to_right():
    pieces = [(300, 12, 360, 30, "1.250,00"), (10, 10, 80, 30, "Un"), (90, 11, 150, 29, "50 kg"),
              (10, 50, 120, 70, "KDV")]

    assert [line.text for line in rows(pieces)] == ["Un 50 kg 1.250,00", "KDV"]
