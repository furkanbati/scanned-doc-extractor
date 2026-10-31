"""Rule and regex field extraction from OCR lines (the E1 baseline).

These rules are specific to the receipt document type. Rules were written by
looking at the dev split only.
"""

import re

from extractor.dataset import OcrLine
from extractor.fields.base import Extraction
from extractor.schema import (
    DAY_MONTH_YEAR,
    MONTH_DAY_YEAR,
    NUMERIC_DATE,
    Fields,
    normalize_date,
)

# SROIE scans carry the annotator's name stamped above the receipt.
ANNOTATOR_STAMP = re.compile(r"^TAN (WOON YANN|CHAY YEE)$")

COMPANY_WORDS = re.compile(
    r"\b(SDN\.?\s*BHD|BHD|BERHAD|S/B|PLT|ENTERPRISE|TRADING|RESTAURANT|"
    r"RESTORAN|STORE|MART|MARKET|SHOP|CAFE|HARDWARE|BAKERY|STATIONERY|"
    r"COMPANY|CO\.|HOLDINGS|INDUSTRIES|SUPPLY|PHARMACY|BOOK)\b"
)
NOT_COMPANY = re.compile(r"\b(INVOICE|RECEIPT|CASH BILL|TAX|GST|TEL|DATE)\b")
COMPANY_CONTINUATION = re.compile(r"^(CO\.|\(M\)|SDN|BHD)")
SDN_MISREAD = re.compile(r"\bS[O0I]N(?=\.?\s*BHD)")

ADDRESS_STOP = re.compile(
    r"\b(TEL|PHONE|TELEPHONE|FAX|H/P|GST|INVOICE|RECEIPT|CASH|BILL|"
    r"DATE|CASHIER|TABLE|DOC|EMAIL|WEBSITE|WWW)\b|@"
)
REGISTRATION = re.compile(r"\b(REG|COMPANY NO|CO\.?\s*NO|ROC|BR NO)\b|\b\d{5,}\s*-\s*[A-Z]\b")
PHONE = re.compile(r"\b0\d{1,2}\s*-\s*\d{3,4}\s?\d{3,4}\b")
LETTERS = re.compile(r"[A-Z]{3,}")

MONEY = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)\s?[.,]\s?(\d{2})(?!\d)")
TOTAL_WORDS = re.compile(r"TOTAL|AMOUNT|NETT|PAYABLE|DUE")
TOTAL_EXCLUDE = re.compile(
    r"SUB\s*-?\s*TOTAL|TOTAL\s*(QTY|QUANTITY|ITEM|DISC|SAVING|GST|TAX|EXCL)|"
    r"(QTY|ITEMS?)\s*:|GST\s*(AMOUNT|PAYABLE)|TAX\s*AMOUNT|DISCOUNT|SUMMARY"
)
TOTAL_STRONG = re.compile(r"ROUND|GRAND|NETT|PAYABLE|DUE|INCL")
NOT_TOTAL_AMOUNT = re.compile(r"CASH|CHANGE|TENDER|PAID|BALANCE|VISA|CARD")
AMOUNT_ONLY = re.compile(r"^[^A-Z0-9]*(RM)?[^A-Z0-9]*[\d,.\s]+[^A-Z0-9]*$")

YEARS = range(2010, 2031)


def extract_fields(lines: list[OcrLine]) -> Fields:
    texts = [
        text
        for line in lines
        if (text := line.text.upper().strip()) and not ANNOTATOR_STAMP.match(text)
    ]
    company, company_end = company_name(texts, find_company(texts))

    return {
        "company": company,
        "address": find_address(texts, company_end),
        "date": find_date(texts),
        "total": find_total(texts),
    }


def find_company(texts: list[str], window: int = 8) -> int | None:
    """First of the top lines that looks like a business name."""

    head = [
        index
        for index, text in enumerate(texts[:window])
        if LETTERS.search(text) and not NOT_COMPANY.search(text)
    ]

    for index in head:
        if COMPANY_WORDS.search(texts[index]):
            return index

    return head[0] if head else None


def company_name(texts: list[str], index: int | None) -> tuple[str | None, int | None]:
    """The company line, joined to the line above or below that continues it.

    Returns the name and the index of its last line.
    """

    if index is None:
        return None, None

    name, last = texts[index], index
    if index > 0 and COMPANY_CONTINUATION.match(name) and LETTERS.search(texts[index - 1]):
        name = f"{texts[index - 1]} {name}"
    elif index + 1 < len(texts) and COMPANY_CONTINUATION.match(texts[index + 1]):
        name, last = f"{name} {texts[index + 1]}", index + 1

    return SDN_MISREAD.sub("SDN", name), last


def is_address_noise(text: str) -> bool:
    """Registration numbers and dates printed between the name and the address."""

    return bool(REGISTRATION.search(text) or find_date([text]))


def find_address(
    texts: list[str],
    company_index: int | None,
    max_lines: int = 5,
) -> str | None:
    """Lines after the company name, until a phone, tax or invoice line."""

    if company_index is None:
        return None

    parts = []
    for text in texts[company_index + 1 : company_index + 1 + max_lines + 2]:
        if not parts and is_address_noise(text):
            continue

        if ADDRESS_STOP.search(text) or PHONE.search(text) or find_date([text]):
            break

        parts.append(text)
        if len(parts) == max_lines:
            break

    if not parts or not any(char.isdigit() for char in " ".join(parts)):
        return None

    return " ".join(parts)


def find_date(texts: list[str]) -> str | None:
    """First substring that parses as a date with a plausible year."""

    for text in texts:
        for pattern in (NUMERIC_DATE, DAY_MONTH_YEAR, MONTH_DAY_YEAR):
            for match in pattern.finditer(text):
                parsed = normalize_date(match.group()) or ""
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", parsed) and int(parsed[:4]) in YEARS:
                    return match.group()

    return None


def money(text: str) -> list[str]:
    return [f"{whole.replace(',', '')}.{cents}" for whole, cents in MONEY.findall(text)]


def total_strength(text: str) -> int:
    if TOTAL_STRONG.search(text):
        return 2

    return 1 if "TOTAL" in text else 0


def find_total(texts: list[str]) -> str | None:
    """Amount on (or right after) the strongest TOTAL line; else the largest amount."""

    candidates = []
    for index, text in enumerate(texts):
        if not TOTAL_WORDS.search(text) or TOTAL_EXCLUDE.search(text):
            continue

        amounts = money(text)
        if not amounts:
            following = texts[index + 1 : index + 3]
            amounts = next(
                (money(line) for line in following if AMOUNT_ONLY.match(line) and money(line)),
                [],
            )

        if amounts:
            candidates.append((total_strength(text), index, amounts[-1]))

    if candidates:
        return max(candidates)[2]

    amounts = [
        float(amount)
        for text in texts
        if not NOT_TOTAL_AMOUNT.search(text)
        for amount in money(text)
    ]

    return f"{max(amounts):.2f}" if amounts else None


class RuleExtractor:
    name = "rules"
    needs_ocr = True

    def extract(self, image, lines: list[OcrLine]) -> Extraction:
        return Extraction(fields=extract_fields(lines))
