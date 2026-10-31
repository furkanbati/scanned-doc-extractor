"""Draw the synthetic receipt in docs/sample_receipt.png (no dataset images in the repo).

Run inside the api container: python docs/make_sample_receipt.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LINES = [
    ("center", "KEDAI RUNCIT MAJU JAYA SDN BHD", 30),
    ("center", "(1234567-X)", 24),
    ("center", "NO. 12, JALAN MERANTI 3,", 24),
    ("center", "TAMAN BUKIT INDAH, 81200 JOHOR BAHRU, JOHOR.", 24),
    ("center", "TEL: 07-123 4567", 24),
    ("center", "TAX INVOICE", 28),
    ("left", "DATE: 14/03/2018   TIME: 18:42", 24),
    ("left", "CASHIER: AMIRA", 24),
    ("rule", "", 0),
    ("item", "MINERAL WATER 1.5L  2 X 2.50|5.00", 24),
    ("item", "WHITE BREAD 400G    1 X 3.90|3.90", 24),
    ("item", "FRESH MILK 1L       1 X 7.25|7.25", 24),
    ("rule", "", 0),
    ("item", "SUB TOTAL|16.15", 24),
    ("item", "ROUNDING ADJ|0.00", 24),
    ("item", "TOTAL ROUNDED|16.15", 28),
    ("item", "CASH|20.00", 24),
    ("item", "CHANGE|3.85", 24),
    ("center", "THANK YOU, PLEASE COME AGAIN", 24),
]


def main() -> None:
    width, height, margin = 720, 980, 40
    image = Image.new("RGB", (width, height), "#fbfaf6")
    draw = ImageDraw.Draw(image)
    y = 40

    for kind, text, size in LINES:
        if kind == "rule":
            draw.line((margin, y + 10, width - margin, y + 10), fill="#444", width=2)
            y += 30
            continue

        font = ImageFont.load_default(size=size)

        if kind == "item":
            left, right = text.split("|")
            draw.text((margin, y), left, fill="#222", font=font)
            draw.text((width - margin, y), right, fill="#222", font=font, anchor="ra")
        elif kind == "center":
            draw.text((width / 2, y), text, fill="#222", font=font, anchor="ma")
        else:
            draw.text((margin, y), text, fill="#222", font=font)

        y += size + 18

    image.save(Path(__file__).parent / "sample_receipt.png")


if __name__ == "__main__":
    main()
