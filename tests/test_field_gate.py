"""A decision for document types without line items (invoices), so a caller
always has one: the fields found and well formed, the sums adding up."""

from fastapi.testclient import TestClient

import api
import catalogue
from extractor.gate import decide_fields, sum_reasons
from extractor.pipeline import PipelineResult
from extractor.review import review_notes
from extractor.schema import load_doctype
from tests.test_doctypes_api import png

INVOICE = load_doctype("invoice")
GOOD = {"company": "ABC GIDA A.Ş.", "tax_id": "1234567890", "invoice_number": "A-1", "date": "12.03.2024",
        "subtotal": "1.000,00", "tax": "200,00", "total": "1.200,00"}


def decide(fields):
    return decide_fields(INVOICE, fields, review_notes(INVOICE, fields))


def test_an_invoice_whose_fields_and_sums_hold_is_accepted():
    assert decide(GOOD).accepted is True


def test_sums_that_do_not_hold_are_a_reason():
    gate = decide(GOOD | {"subtotal": "100,00"})

    assert gate.accepted is False
    assert gate.reasons == ["subtotal 100.00 plus tax 200.00 is not the total 1200.00"]


def test_a_missing_field_is_a_reason_and_a_missing_optional_amount_is_not():
    assert decide(GOOD | {"invoice_number": None}).reasons == ["invoice_number not found"]
    assert decide(GOOD | {"subtotal": None, "tax": None}).accepted is True


def test_a_type_without_the_three_amounts_has_no_sum_to_check():
    assert sum_reasons(load_doctype("receipt"), {"total": "9.00"}) == []


class Model:
    def __init__(self, fields):
        self.fields = fields

    def run(self, image):
        return PipelineResult(fields=self.fields, lines=[])


def test_a_model_reading_an_invoice_gives_a_decision(monkeypatch):
    monkeypatch.setattr(api, "get_typed_pipeline", lambda method, doctype: Model(GOOD | {"total": "9,00"}))
    monkeypatch.setattr(catalogue, "engines", lambda: {"e5": {"available": True}})

    body = TestClient(api.app).post("/extract", files={"file": ("f.png", png(41))},
                                    data={"method": "e5", "doctype": "invoice"}).json()

    assert body["line_items"] is None
    assert body["gate"] == {"accepted": False, "reasons": ["subtotal 1000.00 plus tax 200.00 is not the total 9.00"]}
