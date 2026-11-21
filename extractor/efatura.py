"""e-Fatura: Turkish electronic invoices as UBL-TR XML, read without OCR.

An e-Fatura (and an e-Arşiv invoice) is an XML document in the UBL 2.1
schema with Turkey's profile (GİB). Every value the invoice type asks for is
a tagged element, so it is read as it is: no OCR, no rules, no model.

The XML comes from users, so it is parsed with defusedxml: a document that
declares entities is refused instead of expanded.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from extractor.fields.line_items import LineItem, LineItemResult

UBL = "urn:oasis:names:specification:ubl:schema:xsd:"
INVOICE = "{" + UBL + "Invoice-2}Invoice"
CAC = "{" + UBL + "CommonAggregateComponents-2}"
CBC = "{" + UBL + "CommonBasicComponents-2}"
# Amounts that must agree may differ by this much from rounding.
TOLERANCE = Decimal("0.01")
# The issuer's identifier: a company's tax number, or a person's ID number.
TAX_SCHEMES = ("VKN", "TCKN")


class NotAnInvoice(ValueError):
    """The XML is not a UBL invoice, or it is unsafe to read."""


@dataclass
class EInvoice:
    """The invoice type's fields, the line items, and what else the XML says
    (`meta`: ETTN, currency, scenario, type, buyer)."""

    fields: dict[str, str | None]
    line_items: LineItemResult
    meta: dict[str, str | None] = field(default_factory=dict)


def looks_like_xml(data: bytes) -> bool:
    return data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")


def read_invoice(data: bytes) -> EInvoice:
    try:
        root = fromstring(data)
    except DefusedXmlException as error:
        raise NotAnInvoice(f"Unsafe XML, not read: {type(error).__name__}") from error
    except ParseError as error:
        raise NotAnInvoice(f"Not well-formed XML: {error}") from error
    if root.tag != INVOICE:
        raise NotAnInvoice("Not a UBL invoice (e-Fatura): the root element is " + root.tag.split("}")[-1])

    supplier = root.find(f"{CAC}AccountingSupplierParty/{CAC}Party")
    buyer = root.find(f"{CAC}AccountingCustomerParty/{CAC}Party")
    totals = root.find(f"{CAC}LegalMonetaryTotal")
    tax = _sum(root.findall(f"{CAC}TaxTotal/{CBC}TaxAmount"))
    items = [_line(line) for line in root.findall(f"{CAC}InvoiceLine")]

    fields = {
        "company": _party_name(supplier),
        "tax_id": _party_id(supplier),
        "invoice_number": _text(root, f"{CBC}ID"),
        "date": _text(root, f"{CBC}IssueDate"),
        "subtotal": _text(totals, f"{CBC}TaxExclusiveAmount"),
        "tax": None if tax is None else str(tax),
        "total": _text(totals, f"{CBC}PayableAmount"),
    }
    result = LineItemResult(
        items=items,
        subtotal=_amount(totals, f"{CBC}LineExtensionAmount"),
        tax=tax,
        discount=_amount(totals, f"{CBC}AllowanceTotalAmount"),
        total=_amount(totals, f"{CBC}PayableAmount"),
    )
    result.reasons = disagreements(root, items, tax)
    result.needs_review = bool(result.reasons)
    meta = {
        "ettn": _text(root, f"{CBC}UUID"),
        "currency": _text(root, f"{CBC}DocumentCurrencyCode"),
        "scenario": _text(root, f"{CBC}ProfileID"),
        "type": _text(root, f"{CBC}InvoiceTypeCode"),
        "issue_time": _text(root, f"{CBC}IssueTime"),
        "buyer": _party_name(buyer),
        "buyer_tax_id": _party_id(buyer),
    }

    return EInvoice(fields, result, meta)


def disagreements(root: Element, items: list[LineItem], tax: Decimal | None) -> list[str]:
    """Where the invoice's own amounts do not add up. An e-Fatura is checked by
    GİB, so this is rare; it is said rather than assumed."""

    totals = root.find(f"{CAC}LegalMonetaryTotal")
    if totals is None:
        return ["no totals"]

    def amount(tag: str) -> Decimal | None:
        return _amount(totals, CBC + tag)

    lines, exclusive, inclusive, payable = (amount(tag) for tag in (
        "LineExtensionAmount", "TaxExclusiveAmount", "TaxInclusiveAmount", "PayableAmount"))
    zero = Decimal(0)
    withheld = _sum(root.findall(f"{CAC}WithholdingTaxTotal/{CBC}TaxAmount")) or zero
    reasons = []
    if payable is None:
        reasons.append("no total")
    if items and lines is not None and not _close(sum((item.amount for item in items), zero), lines):
        reasons.append("the items do not add up to the lines' total")
    if lines is not None and exclusive is not None and not _close(
            lines - (amount("AllowanceTotalAmount") or zero) + (amount("ChargeTotalAmount") or zero), exclusive):
        reasons.append("discounts and charges do not explain the amount before tax")
    if exclusive is not None and inclusive is not None and not _close(exclusive + (tax or zero), inclusive):
        reasons.append("amount before tax plus tax is not the amount with tax")
    if inclusive is not None and payable is not None and not _close(
            inclusive - withheld - (amount("PrepaidAmount") or zero) + (amount("PayableRoundingAmount") or zero), payable):
        reasons.append("the amount to pay does not follow from the amount with tax")

    return reasons


def _line(line: Element) -> LineItem:
    discount = sum((_decimal(charge.findtext(f"{CBC}Amount")) or Decimal(0)
                    for charge in line.findall(f"{CAC}AllowanceCharge")
                    if (charge.findtext(f"{CBC}ChargeIndicator") or "").strip().lower() == "false"), Decimal(0))

    return LineItem(
        name=_text(line, f"{CAC}Item/{CBC}Name") or "",
        amount=_amount(line, f"{CBC}LineExtensionAmount") or Decimal(0),
        quantity=_amount(line, f"{CBC}InvoicedQuantity"),
        unit_price=_amount(line, f"{CAC}Price/{CBC}PriceAmount"),
        discount=discount,
    )


def _party_name(party: Element | None) -> str | None:
    if party is None:
        return None
    name = _text(party, f"{CAC}PartyName/{CBC}Name") or _text(party, f"{CAC}PartyLegalEntity/{CBC}RegistrationName")
    if name:
        return name
    person = " ".join(part for part in (_text(party, f"{CAC}Person/{CBC}FirstName"),
                                        _text(party, f"{CAC}Person/{CBC}FamilyName")) if part)

    return person or None


def _party_id(party: Element | None) -> str | None:
    if party is None:
        return None
    for identifier in party.findall(f"{CAC}PartyIdentification/{CBC}ID"):
        if (identifier.get("schemeID") or "").upper() in TAX_SCHEMES and (identifier.text or "").strip():
            return identifier.text.strip()

    return None


def _text(element: Element | None, path: str) -> str | None:
    if element is None:
        return None
    text = element.findtext(path)

    return " ".join(text.split()) or None if text is not None else None


def _decimal(text: str | None) -> Decimal | None:
    try:
        return None if text is None else Decimal(text.strip())
    except InvalidOperation:
        return None


def _amount(element: Element | None, path: str) -> Decimal | None:
    return _decimal(_text(element, path))


def _sum(elements: list[Element]) -> Decimal | None:
    amounts = [amount for amount in (_decimal(element.text) for element in elements) if amount is not None]

    return sum(amounts, Decimal(0)) if amounts else None


def _close(first: Decimal, second: Decimal) -> bool:
    return abs(first - second) <= TOLERANCE
