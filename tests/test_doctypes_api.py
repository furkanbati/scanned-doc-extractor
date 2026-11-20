"""The API for teaching by picking OCR lines, for any document type."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import api
import catalogue
from extractor.fields.template import TemplateExtractor
from extractor.pipeline import Pipeline, PipelineResult
from tests.test_picked import EXAMPLES, invoice, picked

NEW = invoice(171, 28, [2000.0, 500.0])
OTHER = invoice(9, 1, [10.0])[:1]  # a page with none of the format's lines
PAGES = {41: EXAMPLES[0], 42: EXAMPLES[1], 43: EXAMPLES[2], 44: NEW, 45: OTHER}


class Ocr:
    name = "fake"

    def read(self, image):
        return PAGES[image.size[0]]


class E2:
    ocr = Ocr()


class E5:
    doctypes = []

    def __init__(self, doctype):
        self.doctype = doctype

    def run(self, image):
        E5.doctypes.append(self.doctype)
        return PipelineResult(fields={"company": "XYZ LTD", "invoice_number": "9", "date": "01.03.2024",
                                      "total": "12,00"}, lines=[])


def png(width):
    buffer = io.BytesIO()
    Image.new("RGB", (width, 20), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def as_json(lines):
    return [{"text": line.text, "box": list(line.box)} for line in lines]


def example(page, **values):
    return {"lines": as_json(page), "values": values, "picked": picked(page)}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setattr(api, "get_pipeline", lambda method: E2())
    monkeypatch.setattr(api, "get_typed_pipeline", lambda method, doctype: E5(doctype))
    monkeypatch.setattr(api, "build_template_pipeline",
                        lambda template, doctype: Pipeline(Ocr(), TemplateExtractor(template, doctype)))
    monkeypatch.setattr(catalogue, "engines", lambda: {"e5": {"available": True}})
    api.get_template_pipeline.cache_clear()
    E5.doctypes = []

    return TestClient(api.app)


def teach(client, name="ABC Gıda"):
    return client.post("/templates/learn", json={
        "name": name, "doctype": "invoice", "examples": [example(page) for page in EXAMPLES]})


def test_ocr_numbers_the_lines_to_pick_from(client):
    body = client.post("/ocr", files={"file": ("a.png", png(41))}).json()

    assert body["lines"][2]["text"] == "FATURA NO: A-2024-0153"
    assert len(body["lines"][2]["box"]) == 8
    assert body["image_size"] == [41, 20]


def test_document_types_are_listed_and_a_user_can_add_one(client):
    assert {"receipt", "invoice"} <= set(client.get("/doctypes").json()["doctypes"])

    order = {"name": "order", "fields": [{"name": "order_number", "type": "text"},
                                         {"name": "discount", "type": "amount", "optional": True}]}
    assert client.post("/doctypes", json=order).status_code == 200
    assert client.get("/doctypes").json()["doctypes"]["order"]["builtin"] is False
    assert client.post("/doctypes", json=order).status_code == 409
    assert client.post("/doctypes", json={**order, "name": "receipt"}).status_code == 409
    assert client.post("/doctypes", json={**order, "name": "Bad Name"}).status_code == 400


def test_the_next_example_comes_with_its_lines_picked(client):
    body = client.post("/templates/suggest", json={
        "doctype": "invoice", "examples": [example(EXAMPLES[0])], "lines": as_json(EXAMPLES[1])}).json()

    assert {field: found["lines"] for field, found in body["fields"].items()} == picked(EXAMPLES[1])
    assert body["fields"]["invoice_number"]["value"] == "A-2024-0154"


def test_picked_examples_teach_an_invoice_rule_set(client):
    body = teach(client).json()

    assert body["status"] == "saved"
    assert body["template"]["doctype"] == "invoice"
    assert body["skipped"] == []
    assert "invoice_number" in body["template"]["fields"]


def test_an_invoice_is_read_with_its_rule_set(client):
    teach(client)

    body = client.post("/extract", files={"file": ("n.png", png(44))}, data={"template": "abc-gida"}).json()

    assert body["doctype"] == "invoice"
    assert body["fields"]["invoice_number"] == "A-2024-0171"
    assert body["normalized"]["total"] == "3000.00"
    assert body["gate"]["accepted"] is True


def test_an_invoice_it_cannot_read_goes_to_e5_as_an_invoice(client):
    teach(client)

    body = client.post("/extract", files={"file": ("o.png", png(45))}, data={"template": "abc-gida"}).json()

    assert E5.doctypes == ["invoice"]
    assert body["method"] == "e5"
    assert body["doctype"] == "invoice"
    assert body["normalized"]["total"] == "12.00"
    assert "E5 read it instead" in body["warning"]


def test_unknown_document_types_and_too_few_examples_are_refused(client):
    assert client.post("/templates/suggest", json={"doctype": "nope", "examples": [], "lines": []}).status_code == 404
    lesson = {"name": "x", "doctype": "invoice", "examples": [example(EXAMPLES[0])]}
    assert client.post("/templates/learn", json=lesson).status_code == 400


def test_the_value_of_picked_lines_is_read_as_learning_reads_it(client):
    def value(kind, *lines):
        return client.post("/templates/value", json={"type": kind, "lines": list(lines)}).json()["value"]

    assert value("text", "FATURA NO: A-2024-0153") == "A-2024-0153"
    assert value("amount", "GENEL TOPLAM 1.200,00") == "1.200,00"
    assert value("date", "TOPLAM") is None
