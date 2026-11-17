"""Money amounts as printed on receipts, read the same way for labels and predictions.

Receipts write thousands and decimals with either separator: "25,000",
"25.000", "1,591,600", "99,999.99", "9.999,99", "Rp. 41.580". The rule is
positional, not national: the last separator is a decimal point only when one
or two digits follow it and, if there are several separators, it differs from
the one before it ("1,234.56"). A single separator followed by three digits
("25.000") always groups thousands.
"""

import re
from decimal import Decimal, InvalidOperation

# A number with its separators; an OCR'd space after a separator is allowed ("16, 500").
NUMBER = re.compile(r"\d(?:[.,]\s(?=\d)|[\d.,])*")
SEPARATORS = ".,"

# A token that is clearly money: thousands groups or a decimal part.
# Plain integers ("15000", "9") are numbers too, but only count as money when
# they line up with other amounts (see extractor/fields/line_items.py).
# Up to two stray marks around a number are allowed: a sign, a bracket, or
# OCR noise such as ".20,000" or "£12,273".
EDGE = r"[^0-9A-Za-z]{0,2}"
STRONG_MONEY = re.compile(
    rf"^{EDGE}(?:RP\.?|IDR|@)?\s*{EDGE}"
    r"(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+[.,]\d{2})"
    rf"\s*{EDGE}$",
    re.IGNORECASE,
)
PLAIN_NUMBER = re.compile(rf"^{EDGE}(?:RP\.?|IDR|@)?\s*{EDGE}\d+\s*{EDGE}$", re.IGNORECASE)


def _to_decimal(number: str) -> Decimal | None:
    number = re.sub(r"\s", "", number).rstrip(SEPARATORS)
    positions = [index for index, char in enumerate(number) if char in SEPARATORS]

    if not positions:
        digits = number
    else:
        last = positions[-1]
        tail = len(number) - last - 1
        previous = number[positions[-2]] if len(positions) > 1 else None
        is_decimal = tail in (1, 2) and previous != number[last]

        if is_decimal:
            whole = re.sub(r"[.,]", "", number[:last])
            digits = f"{whole}.{number[last + 1:]}"
        else:
            digits = re.sub(r"[.,]", "", number)

    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def parse_amount(text: str | None) -> Decimal | None:
    """The last number in the text as a Decimal, negative when marked with '-'.

    "16,500" -> 16500, "Rp. 99.999" -> 99999, "9.999,99" -> 9999.99,
    "1,400 -" -> -1400, "-16,000" -> -16000. None when there is no number.
    """

    if text is None:
        return None

    matches = list(NUMBER.finditer(text))
    if not matches:
        return None

    match = matches[-1]
    value = _to_decimal(match.group())
    if value is None:
        return None

    before = text[: match.start()].rstrip()
    after = text[match.end():].lstrip()
    if before.endswith("-") or after.startswith("-") or (before.endswith("(") and after.startswith(")")):
        value = -value

    return value


def is_money(token: str) -> bool:
    """Thousands groups or a decimal part: '16,500', '9.999,99', 'Rp.41.580', '12.50'."""

    return bool(STRONG_MONEY.match(token.strip()))


def is_number(token: str) -> bool:
    """Money, or a plain integer that could be an amount ('15000', '0')."""

    token = token.strip()

    return bool(STRONG_MONEY.match(token) or PLAIN_NUMBER.match(token))
