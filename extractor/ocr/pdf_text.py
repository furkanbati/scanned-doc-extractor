"""The text layer of a digital PDF, as OCR lines: no OCR, no misread letters.

A PDF made by software (an e-Arşiv invoice, an exported bill) carries its
text and where each piece is drawn. The pieces on one row are joined into a
line, and the boxes are scaled to the page rendered at `dpi`, so they match
the image the engines and the UI see.

A page with too little text is a scan (or a picture); its lines are read by
OCR instead. So is a rotated page, whose text coordinates do not follow the
rendered image.
"""

import pypdfium2

from extractor.ocr.base import OcrLine, rect_box

# Letters and digits a page needs to count as text rather than a scan.
MIN_TEXT_CHARS = 20


def pdf_text_lines(data: bytes, dpi: int) -> list[OcrLine] | None:
    """The first page's lines from its text layer, or None when it has none."""

    try:
        pdf = pypdfium2.PdfDocument(data)
    except pypdfium2.PdfiumError:
        return None
    try:
        if len(pdf) == 0:
            return None
        page = pdf[0]
        if page.get_rotation() % 360:
            return None
        text = page.get_textpage()
        if sum(char.isalnum() for char in text.get_text_range()) < MIN_TEXT_CHARS:
            return None
        scale = dpi / 72
        height = page.get_height()
        pieces = []
        for index in range(text.count_rects()):
            left, bottom, right, top = text.get_rect(index)
            piece = " ".join(text.get_text_bounded(left, bottom, right, top).split())
            if piece:
                pieces.append((round(left * scale), round((height - top) * scale),
                               round(right * scale), round((height - bottom) * scale), piece))
    finally:
        pdf.close()

    return rows(pieces)


def rows(pieces: list[tuple[int, int, int, int, str]]) -> list[OcrLine]:
    """Pieces whose middle falls within a row's height make one line, left to
    right; rows top to bottom."""

    grouped: list[list[tuple[int, int, int, int, str]]] = []
    for piece in sorted(pieces, key=lambda piece: (piece[1], piece[0])):
        middle = (piece[1] + piece[3]) / 2
        row = next((row for row in grouped
                    if min(p[1] for p in row) <= middle <= max(p[3] for p in row)), None)
        if row is None:
            grouped.append([piece])
        else:
            row.append(piece)

    lines = []
    for row in grouped:
        row.sort(key=lambda piece: piece[0])
        box = rect_box(min(p[0] for p in row), min(p[1] for p in row), max(p[2] for p in row), max(p[3] for p in row))
        lines.append(OcrLine(text=" ".join(piece[4] for piece in row), box=box))

    return lines
