"""e-Fatura XML (UBL-TR), read without OCR. The invoices are made up."""

from decimal import Decimal

import pytest

from extractor.efatura import NotAnInvoice, looks_like_xml, read_invoice
from extractor.schema import load_doctype

NAMESPACES = ('xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
              'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
              'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"')
SUPPLIER = """<cac:PartyIdentification><cbc:ID schemeID="VKN">1234567890</cbc:ID></cac:PartyIdentification>
      <cac:PartyIdentification><cbc:ID schemeID="MERSISNO">0123456789000015</cbc:ID></cac:PartyIdentification>
      <cac:PartyName><cbc:Name>ABC Gıda Sanayi ve Ticaret A.Ş.</cbc:Name></cac:PartyName>"""
PERSON = """<cac:PartyIdentification><cbc:ID schemeID="TCKN">11111111110</cbc:ID></cac:PartyIdentification>
      <cac:Person><cbc:FirstName>Ayşe</cbc:FirstName><cbc:FamilyName>Yılmaz</cbc:FamilyName></cac:Person>"""


def line(number, name, quantity, price, amount, discount=None):
    allowance = (f"<cac:AllowanceCharge><cbc:ChargeIndicator>false</cbc:ChargeIndicator>"
                 f'<cbc:Amount currencyID="TRY">{discount}</cbc:Amount></cac:AllowanceCharge>') if discount else ""
    return f"""<cac:InvoiceLine>
    <cbc:ID>{number}</cbc:ID>
    <cbc:InvoicedQuantity unitCode="KGM">{quantity}</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="TRY">{amount}</cbc:LineExtensionAmount>
    {allowance}
    <cac:TaxTotal><cbc:TaxAmount currencyID="TRY">0</cbc:TaxAmount></cac:TaxTotal>
    <cac:Item><cbc:Name>{name}</cbc:Name></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="TRY">{price}</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>"""


LINES = line(1, "Un 50 kg", 1, "1250.00", "1250.00") + line(2, "Şeker 25 kg", 1, "1000.00", "980.50", "19.50")


def invoice(supplier=SUPPLIER, lines=LINES, tax="446.10", payable="2676.60", inclusive="2676.60", extra="",
            type_code="SATIS"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice {NAMESPACES}>
  <cbc:UBLVersionID>2.1</cbc:UBLVersionID>
  <cbc:ProfileID>TICARIFATURA</cbc:ProfileID>
  <cbc:ID>ABC2024000000153</cbc:ID>
  <cbc:UUID>f47ac10b-58cc-4372-a567-0e02b2c3d479</cbc:UUID>
  <cbc:IssueDate>2024-03-12</cbc:IssueDate>
  <cbc:IssueTime>14:05:00</cbc:IssueTime>
  <cbc:InvoiceTypeCode>{type_code}</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>TRY</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
      {supplier}
  </cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party>
      <cac:PartyIdentification><cbc:ID schemeID="VKN">9876543210</cbc:ID></cac:PartyIdentification>
      <cac:PartyName><cbc:Name>XYZ Market Ltd. Şti.</cbc:Name></cac:PartyName>
  </cac:Party></cac:AccountingCustomerParty>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="TRY">{tax}</cbc:TaxAmount>
  </cac:TaxTotal>
  {extra}
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="TRY">2230.50</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="TRY">2230.50</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="TRY">{inclusive}</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="TRY">{payable}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  {lines}
</Invoice>""".encode("utf-8")


def test_the_invoice_fields_are_read_from_the_xml():
    read = read_invoice(invoice())

    assert read.fields == {
        "company": "ABC Gıda Sanayi ve Ticaret A.Ş.",
        "tax_id": "1234567890",
        "invoice_number": "ABC2024000000153",
        "date": "2024-03-12",
        "subtotal": "2230.50",
        "tax": "446.10",
        "total": "2676.60",
    }
    normalized = load_doctype("invoice").normalize(read.fields)
    assert normalized["date"] == "2024-03-12"
    assert normalized["total"] == "2676.60"


def test_line_items_and_the_rest_of_the_invoice_are_read():
    read = read_invoice(invoice())

    assert [(item.name, item.quantity, item.unit_price, item.amount, item.discount) for item in read.line_items.items] == [
        ("Un 50 kg", Decimal(1), Decimal("1250.00"), Decimal("1250.00"), Decimal(0)),
        ("Şeker 25 kg", Decimal(1), Decimal("1000.00"), Decimal("980.50"), Decimal("19.50")),
    ]
    assert read.line_items.total == Decimal("2676.60")
    assert read.line_items.needs_review is False
    assert read.meta == {"ettn": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "currency": "TRY",
                         "scenario": "TICARIFATURA", "type": "SATIS", "issue_time": "14:05:00",
                         "buyer": "XYZ Market Ltd. Şti.", "buyer_tax_id": "9876543210"}


def test_a_person_issuing_the_invoice_is_named_with_their_id_number():
    read = read_invoice(invoice(supplier=PERSON))

    assert read.fields["company"] == "Ayşe Yılmaz"
    assert read.fields["tax_id"] == "11111111110"


def test_any_namespace_prefix_is_read():
    data = invoice().decode().replace("cac:", "a:").replace("xmlns:cac", "xmlns:a").replace("cbc:", "b:") \
        .replace("xmlns:cbc", "xmlns:b")

    assert read_invoice(data.encode()).fields["total"] == "2676.60"


def test_withholding_lowers_the_amount_to_pay():
    withheld = ('<cac:WithholdingTaxTotal><cbc:TaxAmount currencyID="TRY">223.05</cbc:TaxAmount>'
                "</cac:WithholdingTaxTotal>")

    read = read_invoice(invoice(payable="2453.55", extra=withheld))

    assert read.fields["total"] == "2453.55"
    assert read.line_items.reasons == []


def test_amounts_that_do_not_add_up_are_said():
    read = read_invoice(invoice(tax="400.00"))

    assert read.line_items.needs_review is True
    assert read.line_items.reasons == ["amount before tax plus tax is not the amount with tax"]


def test_missing_optional_values_stay_empty():
    read = read_invoice(invoice(lines="", supplier="<cac:PartyName><cbc:Name>ABC</cbc:Name></cac:PartyName>"))

    assert read.fields["tax_id"] is None
    assert read.line_items.items == []
    assert read.fields["company"] == "ABC"


def test_a_return_invoice_says_so():
    assert read_invoice(invoice(type_code="IADE")).meta["type"] == "IADE"


def test_other_xml_is_not_an_invoice():
    with pytest.raises(NotAnInvoice, match="root element is note"):
        read_invoice(b"<note><to>x</to></note>")
    with pytest.raises(NotAnInvoice, match="Not well-formed"):
        read_invoice(b"<Invoice>")


def test_xml_with_entities_is_refused_not_expanded():
    bomb = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<Invoice>&lol2;</Invoice>"""

    with pytest.raises(NotAnInvoice, match="Unsafe XML"):
        read_invoice(bomb)


def test_xml_is_told_apart_from_images_and_pdfs():
    assert looks_like_xml(b"\xef\xbb\xbf<?xml version='1.0'?><a/>")
    assert looks_like_xml(b"  \n<Invoice/>")
    assert not looks_like_xml(b"%PDF-1.7")
    assert not looks_like_xml(b"\x89PNG\r\n")
