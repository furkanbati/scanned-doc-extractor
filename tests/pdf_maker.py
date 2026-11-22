"""A small PDF with a text layer, written by hand for tests (no PDF library).

`text_pdf([(x, y, size, text), ...])`: lines at points from the top left of
an A4 page, in Helvetica. Text is Latin-1 (WinAnsi), as standard fonts take.
"""

PAGE_WIDTH, PAGE_HEIGHT = 595, 842


def _escape(text: str) -> bytes:
    return text.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def text_pdf(lines: list[tuple[float, float, float, str]]) -> bytes:
    content = b"".join(b"BT /F1 %g Tf %g %g Td (" % (size, x, PAGE_HEIGHT - y - size) + _escape(text) + b") Tj ET\n"
                       for x, y, size, text in lines)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 4 0 R >> >> "
        b"/Contents 5 0 R >>" % (PAGE_WIDTH, PAGE_HEIGHT),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)

    return bytes(out)
