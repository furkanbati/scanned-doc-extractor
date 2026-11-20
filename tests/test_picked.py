"""Rule sets taught by picking the OCR lines each value is printed on, for
any document type: here Turkish invoices with fields receipts do not have."""

import pytest

from extractor.ocr.base import OcrLine, rect_box
from extractor.schema import load_doctype
from extractor.templates.learn import Example, learn, suggest
from extractor.templates.read import decide, extract
from extractor.templates.values import alike, value_from_lines, words

INVOICE = load_doctype("invoice")
FIELDS = {"company": 0, "tax_id": 1, "invoice_number": 2, "date": 3, "subtotal": 6, "tax": 7, "total": 8}


def lines(*texts):
    return [OcrLine(text=text, box=rect_box(0, 20 * i, 300, 20 * i + 15)) for i, text in enumerate(texts)]


def invoice(number, day, items, discount=None):
    subtotal = sum(items)
    tax = subtotal / 5
    money = lambda value: f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    texts = ["ABC GIDA SANAYİ A.Ş.", "VERGİ NO: 1234567890", f"FATURA NO: A-2024-{number:04d}",
             f"FATURA TARİHİ: {day}.03.2024"]
    texts += [f"ÜRÜN {i + 1} {money(item)}" for i, item in enumerate(items)][:2]
    while len(texts) < 6:
        texts.append("AÇIKLAMA: -")
    texts += [f"ARA TOPLAM {money(subtotal)}", f"KDV %20 {money(tax)}", f"GENEL TOPLAM {money(subtotal + tax)}"]
    if discount:
        texts.insert(7, f"İSKONTO {money(discount)}")
    texts.append("BU FATURA ELEKTRONİK ORTAMDA OLUŞTURULMUŞTUR")
    return lines(*texts)


def picked(page):
    """The lines a user picks: each field by its label."""

    labels = {"company": "ABC", "tax_id": "VERGİ", "invoice_number": "FATURA NO", "date": "FATURA TARİHİ",
              "subtotal": "ARA TOPLAM", "tax": "KDV", "total": "GENEL TOPLAM"}
    return {field: [next(i for i, line in enumerate(page) if line.text.startswith(label))]
            for field, label in labels.items()}


EXAMPLES = [invoice(153, 12, [1000.0]), invoice(154, 14, [250.0, 750.5]), invoice(160, 20, [99.9])]


def test_picking_lines_teaches_fields_a_receipt_does_not_have():
    examples = [Example(page, {}, picked(page)) for page in EXAMPLES]

    learned = learn("ABC Gıda", INVOICE, examples)

    assert learned.unstable == {}
    assert learned.template["doctype"] == "invoice"
    fields = extract(learned.template, INVOICE, invoice(171, 28, [2000.0, 500.0]))
    assert fields["invoice_number"] == "A-2024-0171"
    assert fields["tax_id"] == "1234567890"
    assert fields["company"] == "ABC GIDA SANAYİ A.Ş."
    assert INVOICE.normalize(fields)["total"] == "3000.00"
    assert INVOICE.normalize(fields)["date"] == "2024-03-28"


def test_a_value_the_user_corrected_wins_over_the_line():
    examples = [Example(page, {"company": "ABC Gıda Sanayi A.Ş."}, picked(page)) for page in EXAMPLES]

    template = learn("ABC Gıda", INVOICE, examples).template

    assert template["fields"]["company"] == {"kind": "constant", "value": "ABC Gıda Sanayi A.Ş.", "lines": 1}


def test_an_optional_field_on_some_examples_only():
    pages = [invoice(153, 12, [1000.0], discount=50.0), invoice(154, 14, [800.0], discount=20.0), invoice(160, 20, [99.9])]
    examples = []
    for page in pages:
        pick = picked(page)
        found = [i for i, line in enumerate(page) if line.text.startswith("İSKONTO")]
        if found:
            pick["discount"] = found
        examples.append(Example(page, {}, pick))
    doctype = INVOICE.model_copy(update={"fields": [*INVOICE.fields, INVOICE.fields[5].model_copy(update={"name": "discount"})]})

    learned = learn("ABC Gıda", doctype, examples)

    assert learned.unstable == {}
    assert "discount" in learned.template["fields"]
    page = invoice(171, 28, [500.0])
    assert extract(learned.template, doctype, page)["discount"] is None
    assert decide(learned.template, doctype, page, {}).accepted


def test_an_optional_field_on_one_example_is_left_out():
    examples = [Example(page, {}, {k: v for k, v in picked(page).items() if k != "tax_id" or n == 0})
                for n, page in enumerate(EXAMPLES)]
    for example in examples[1:]:
        example.values["tax_id"] = None

    learned = learn("ABC Gıda", INVOICE, [Example(e.lines, {"tax_id": None} if n else {}, e.picked)
                                          for n, e in enumerate(examples)])

    assert learned.skipped == ["tax_id"]
    assert "tax_id" not in learned.template["fields"]


def test_a_missing_required_value_still_stops_learning():
    examples = [Example(page, {}, picked(page)) for page in EXAMPLES]
    examples[1] = Example(EXAMPLES[1], {}, {k: v for k, v in picked(EXAMPLES[1]).items() if k != "total"})

    assert learn("ABC Gıda", INVOICE, examples).unstable == {"total": "a value is missing on an example"}


@pytest.mark.parametrize("kind, picked_lines, value", [
    ("text", ["Fatura No: A-153"], "A-153"),
    ("text", ["Vergi Dairesi: Selçuk Vergi No: 1234567890"], "1234567890"),
    ("text", ["Organize Sanayi Bölgesi 5. Cadde No: 12 Konya"], "Organize Sanayi Bölgesi 5. Cadde No: 12 Konya"),
    ("text", ["NO 1, JALAN SAGU", "81200 JOHOR"], "NO 1, JALAN SAGU 81200 JOHOR"),
    ("text", ["DATE: 12/03 TIME: 10:00"], "10:00"),
    ("text", ["Saat 10:30 Masa 4"], "Saat 10:30 Masa 4"),
    ("amount", ["GENEL TOPLAM 1.200,00 TL"], "1.200,00"),
    ("date", ["FATURA TARİHİ: 12.03.2024"], "12.03.2024"),
    ("amount", ["TOPLAM"], None),
])
def test_the_value_is_taken_from_the_picked_lines(kind, picked_lines, value):
    assert value_from_lines(kind, picked_lines) == value


def test_identifiers_must_match_digit_for_digit():
    assert not alike("A-2024-0153", "A-2024-0157")
    assert alike("A-2024-0153", "A 2024 0153")
    assert alike("KEDAI BUKU MAJU SDN BH0", "KEDAI BUKU MAJU SDN BHD")


def test_words_in_any_alphabet():
    assert words("FATURA TARİHİ: 12.03.2024") == ["FATURA", "TARİHİ"]


def test_after_one_example_the_next_ones_come_with_their_lines_picked():
    first = Example(EXAMPLES[0], {}, picked(EXAMPLES[0]))

    suggested = suggest(INVOICE, [first], EXAMPLES[1])

    assert {field: found["lines"] for field, found in suggested.items()} == picked(EXAMPLES[1])
    assert suggested["invoice_number"]["value"] == "A-2024-0154"
    assert suggested["total"]["value"] == "1.200,60"


def test_nothing_is_suggested_before_any_example():
    assert suggest(INVOICE, [], EXAMPLES[1]) == {}
