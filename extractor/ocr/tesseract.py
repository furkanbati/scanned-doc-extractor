import pytesseract
from PIL import Image
from pytesseract import Output

from extractor.dataset import OcrLine
from extractor.ocr.base import rect_box


class TesseractOcr:
    name = "tesseract"

    def __init__(self, lang: str = "eng", config: str = ""):
        self.lang = lang
        self.config = config

    def read(self, image: Image.Image) -> list[OcrLine]:
        data = pytesseract.image_to_data(
            image,
            lang=self.lang,
            config=self.config,
            output_type=Output.DICT,
        )

        return group_words(data)

    def version(self) -> str:
        return str(pytesseract.get_tesseract_version())


def group_words(data: dict[str, list]) -> list[OcrLine]:
    """Join Tesseract's word rows into lines, keeping Tesseract's reading order."""

    lines: dict[tuple[int, int, int], list[int]] = {}

    for index, text in enumerate(data["text"]):
        if not str(text).strip() or float(data["conf"][index]) < 0:
            continue

        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        lines.setdefault(key, []).append(index)

    result = []
    for indices in lines.values():
        left = min(data["left"][i] for i in indices)
        top = min(data["top"][i] for i in indices)
        right = max(data["left"][i] + data["width"][i] for i in indices)
        bottom = max(data["top"][i] + data["height"][i] for i in indices)

        result.append(
            OcrLine(
                text=" ".join(str(data["text"][i]).strip() for i in indices),
                box=rect_box(left, top, right, bottom),
            )
        )

    return result
