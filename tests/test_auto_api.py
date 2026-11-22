"""learning=auto through the API: a teacher's accepted answers teach rule sets."""

from decimal import Decimal

import pytest

import api
from extractor.fields.line_items import LineItem, LineItemResult
from extractor.pipeline import PipelineResult
from extractor.templates import auto
from extractor.templates import store
from tests.test_auto import teacher
from tests.test_doctypes_api import client, png  # noqa: F401  (client is a fixture)

# The pages tests/test_doctypes_api.py serves by image width, as the teacher reads them.
TRUTH = {41: (153, 12, [1000.0]), 42: (154, 14, [250.0, 750.5]), 43: (160, 20, [99.9]), 44: (171, 28, [2000.0, 500.0])}


class Teacher:
    """E5 reading an invoice: its fields and items, all adding up."""

    wrong_total = False

    def __init__(self, doctype):
        self.doctype = doctype

    def run(self, image):
        number, day, items = TRUTH[image.size[0]]
        fields = teacher(number, day, items)
        subtotal = Decimal(str(sum(items)))
        total = subtotal * Decimal("1.2")
        if Teacher.wrong_total:
            fields["total"], total = "1.234,00", Decimal("1234.00")
            subtotal = total / Decimal("1.2")
        result = LineItemResult(items=[LineItem(name="ÜRÜN", amount=subtotal)], subtotal=subtotal,
                                tax=total - subtotal, total=total, needs_review=False)
        return PipelineResult(fields=fields, lines=[], line_items=result)


@pytest.fixture
def learning(client, monkeypatch):
    monkeypatch.setattr(api, "get_typed_pipeline", lambda method, doctype: Teacher(doctype))
    Teacher.wrong_total = False

    def send(width):
        return client.post("/extract", files={"file": ("d.png", png(width))},
                           data={"learning": "auto", "doctype": "invoice", "method": "e5"}).json()

    return send


def teach_three(send):
    return [send(width) for width in (41, 42, 43)]


def test_three_accepted_answers_teach_a_rule_set(learning):
    first, second, third = teach_three(learning)

    assert first["method"] == "e5" and first["format"] is None
    assert (first["learning"]["examples"], second["learning"]["examples"]) == (1, 2)
    assert third["learning"]["learned"] == "abc-gida-sanayi-a-s-auto"
    assert store.load("abc-gida-sanayi-a-s-auto")["auto"] is True


def test_the_next_document_is_read_by_the_rule_set_and_checked_on_probation(learning):
    teach_three(learning)

    body = learning(44)

    assert body["format"]["rule_set"] == "abc-gida-sanayi-a-s-auto"
    assert body["method"] == "template:abc-gida-sanayi-a-s-auto"
    assert body["fields"]["invoice_number"] == "A-2024-0171"
    assert body["learning"]["probation"] == {"rule_set": "abc-gida-sanayi-a-s-auto", "agreed": True,
                                             "left": auto.PROBATION - 1, "removed": False}
    assert body["chain"][-1]["probation"] is True
    assert body["warning"] is None


def test_a_rule_set_that_disagrees_with_the_teacher_is_removed(learning):
    teach_three(learning)
    Teacher.wrong_total = True

    body = learning(44)

    assert body["method"] == "e5"
    assert body["fields"]["total"] == "1.234,00"
    assert body["learning"]["probation"]["removed"] is True
    assert "removed" in body["warning"]
    assert not store.path("abc-gida-sanayi-a-s-auto").exists()


def test_after_probation_some_reads_are_audited(learning, monkeypatch):
    teach_three(learning)
    template = store.load("abc-gida-sanayi-a-s-auto")
    store.save({**template, "probation": 0}, replace=True)
    monkeypatch.setattr(auto, "AUDIT_SHARE", 1.0)

    body = learning(44)

    assert body["audit"] is True
    assert body["learning"] is None
    assert body["method"] == "template:abc-gida-sanayi-a-s-auto"


def test_learning_is_off_unless_asked(client, monkeypatch):
    monkeypatch.setattr(api, "get_typed_pipeline", lambda method, doctype: Teacher(doctype))
    body = client.post("/extract", files={"file": ("d.png", png(41))},
                       data={"doctype": "invoice", "method": "e5"}).json()

    assert body["learning"] is None
    assert auto.groups() == []


def test_wrong_values_are_refused(client):
    assert client.post("/extract", files={"file": ("d.png", png(41))}, data={"learning": "maybe"}).status_code == 400
    assert client.post("/extract", files={"file": ("d.png", png(41))}, data={"doctype": "nope"}).status_code == 404


def test_the_list_shows_automatic_rule_sets_and_waiting_formats(learning, client):
    learning(41)
    waiting = client.get("/templates").json()
    assert waiting["pending"][0]["examples"] == 1
    assert waiting["pending"][0]["issuer"] == "ABC GIDA SANAYİ A.Ş."

    learning(42)
    learning(43)
    saved = client.get("/templates").json()
    assert saved["pending"] == []
    assert saved["templates"]["abc-gida-sanayi-a-s-auto"]["auto"] is True
    assert saved["templates"]["abc-gida-sanayi-a-s-auto"]["probation"] == auto.PROBATION


def test_a_waiting_format_can_be_forgotten(learning, client):
    group = learning(41)["learning"]["group"]

    assert client.delete(f"/templates/pending/{group}").json() == {"deleted": group}
    assert client.get("/templates").json()["pending"] == []
    assert client.delete(f"/templates/pending/{group}").status_code == 404
    assert client.delete("/templates/pending/..%2Fx").status_code == 404
