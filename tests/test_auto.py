"""Rule sets learned without a user, from a teacher's answers. Made-up invoices."""

import pytest

from extractor.schema import load_doctype
from extractor.templates import auto, store
from extractor.templates.read import extract
from tests.test_picked import invoice, lines

INVOICE = load_doctype("invoice")


def money(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def teacher(number, day, items):
    """What a model reads on invoice(number, day, items)."""

    subtotal = sum(items)
    return {"company": "ABC GIDA SANAYİ A.Ş.", "tax_id": "1234567890", "invoice_number": f"A-2024-{number:04d}",
            "date": f"{day}.03.2024", "subtotal": money(subtotal), "tax": money(subtotal / 5),
            "total": money(subtotal * 1.2)}


def other_format(number):
    return lines("XYZ TEKSTİL LTD. ŞTİ.", "Atatürk Caddesi No 5 İzmir", f"Belge: X-{number}",
                 "Tarih 01.04.2024", "Toplam Tutar 500,00", "Teşekkür ederiz, yine bekleriz")


DOCUMENTS = [(153, 12, [1000.0]), (154, 14, [250.0, 750.5]), (160, 20, [99.9])]


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPLATES_DIR", str(tmp_path))


def observe(number, day, items):
    return auto.observe(invoice(number, day, items), teacher(number, day, items), INVOICE, "e5")


def test_documents_of_one_format_are_grouped_and_a_rule_set_is_learned_at_three():
    first = observe(*DOCUMENTS[0])
    second = observe(*DOCUMENTS[1])
    third = observe(*DOCUMENTS[2])

    assert first["group"] == second["group"] == third["group"]
    assert (first["examples"], second["examples"]) == (1, 2)
    assert first["learned"] is None and second["learned"] is None
    assert third["learned"] == "abc-gida-sanayi-a-s-auto"
    template = store.load(third["learned"])
    assert template["auto"] is True
    assert template["probation"] == auto.PROBATION
    assert extract(template, INVOICE, invoice(171, 28, [2000.0, 500.0]))["invoice_number"] == "A-2024-0171"
    # The group is gone once its rule set is learned.
    assert auto.groups() == []


def test_another_format_starts_its_own_group():
    observe(*DOCUMENTS[0])
    status = auto.observe(other_format(1), {"company": "XYZ TEKSTİL LTD. ŞTİ."}, INVOICE, "e5")

    assert len(auto.groups()) == 2
    assert status["examples"] == 1


def test_format_likeness_tells_one_layout_from_another():
    same = auto.format_likeness(invoice(153, 12, [1000.0]), invoice(154, 14, [250.0, 750.5]))
    different = auto.format_likeness(invoice(153, 12, [1000.0]), other_format(1))

    assert same >= auto.GROUP_ALIKE > different


def test_examples_that_do_not_agree_wait_for_more():
    observe(*DOCUMENTS[0])
    observe(*DOCUMENTS[1])
    wrong = teacher(*DOCUMENTS[2]) | {"total": "1,00"}

    status = auto.observe(invoice(*DOCUMENTS[2]), wrong, INVOICE, "e5")

    assert status["learned"] is None
    assert "total" in status["unstable"]
    assert auto.groups()[0]["examples"].__len__() == 3


def test_a_full_group_drops_an_example_to_make_room():
    for number in range(auto.MAX_EXAMPLES):
        auto.observe(invoice(200 + number, 10, [100.0]), teacher(200 + number, 10, [100.0]) | {"total": str(number)},
                     INVOICE, "e5")

    assert len(auto.groups()[0]["examples"]) == auto.MAX_EXAMPLES - 1


def test_the_name_is_unique():
    store.save({"name": "ABC GIDA SANAYİ A.Ş. (auto)", "doctype": "invoice", "fields": {}, "fingerprint": []})

    for document in DOCUMENTS:
        status = observe(*document)

    assert status["learned"] == "abc-gida-sanayi-a-s-auto-2"


def test_probation_counts_down_on_agreement_and_removes_on_disagreement():
    for document in DOCUMENTS:
        status = observe(*document)
    file = status["learned"]
    template = store.load(file)
    new = invoice(171, 28, [2000.0, 500.0])
    read = extract(template, INVOICE, new)

    agreed = auto.check(file, template, INVOICE, read, teacher(171, 28, [2000.0, 500.0]))
    assert agreed == {"rule_set": file, "agreed": True, "left": auto.PROBATION - 1, "removed": False}

    differs = auto.check(file, store.load(file), INVOICE, read, teacher(171, 28, [2000.0, 500.0]) | {"total": "9,99"})
    assert differs["removed"] is True and differs["differ"] == ["total"]
    assert not store.path(file).exists()


def test_audit_only_after_probation(monkeypatch):
    monkeypatch.setattr(auto, "AUDIT_SHARE", 1.0)

    assert auto.audit({"auto": True, "probation": 0}) is True
    assert auto.audit({"auto": True, "probation": 2}) is False
    assert auto.audit({"name": "taught by a user"}) is False


def test_a_teacher_answer_is_an_example_when_its_fields_and_sums_hold():
    good = teacher(153, 12, [1000.0])

    assert auto.teacher_checks(INVOICE, good) == []
    assert auto.teacher_checks(INVOICE, good | {"subtotal": "250,00"}) == [
        "subtotal 250.00 plus tax 200.00 is not the total 1200.00"]
    assert auto.teacher_checks(INVOICE, good | {"invoice_number": None}) == ["invoice_number not found"]
    # A missing optional amount leaves the sum unchecked.
    assert auto.teacher_checks(INVOICE, good | {"subtotal": None}) == []


def test_receipts_have_no_sum_to_check():
    receipt = load_doctype("receipt")

    assert auto.teacher_checks(receipt, {"company": "A", "address": "B", "date": "01/02/2024", "total": "9.00"}) == []
