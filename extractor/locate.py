"""Find which OCR lines an extracted value came from, to draw them on the image."""

from extractor.dataset import OcrLine
from extractor.schema import Fields, loose


def locate_fields(fields: Fields, lines: list[OcrLine], minimum: int = 4) -> dict[str, list[int]]:
    """Indexes of the lines that make up each field's value.

    A line belongs to a value when one contains the other once spacing and
    punctuation are ignored ("TOTAL 33.90" holds the total "33.90"; an address
    holds each of its lines). Lines shorter than `minimum` letters and digits
    are skipped, so "RM" does not match every amount.
    """

    loose_lines = [loose(line.text) or "" for line in lines]
    located = {}

    for field, value in fields.items():
        target = loose(value)
        if target is None:
            located[field] = []
            continue

        located[field] = [
            index
            for index, text in enumerate(loose_lines)
            # A value as short as "9.00" (900) is still worth finding in a line.
            if len(text) >= minimum and (text in target or (len(target) >= 3 and target in text))
        ]

    return located
