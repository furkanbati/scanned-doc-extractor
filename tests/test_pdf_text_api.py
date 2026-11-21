"""A digital PDF's text layer stands in for OCR in the API."""

import pytest
from fastapi.testclient import TestClient

import api
from extractor.fields.base import Extraction
from extractor.pipeline import Pipeline
from tests.pdf_maker import text_pdf
from tests.test_pdf_text import INVOICE


class Ocr:
    name = "fake"
    calls = 0

    def read(self, image):
        Ocr.calls += 1
        return []


class Rules:
    name = "rules"
    needs_ocr = True
    seen = None

    def extract(self, image, lines):
        Rules.seen = [line.text for line in lines]
        return Extraction(fields={"company": lines[0].text if lines else None})


class Vision:
    name = "vision"
    needs_ocr = False

    def extract(self, image, lines):
        return Extraction(fields={"company": "read from the image"})


@pytest.fixture
def client(monkeypatch):
    pipelines = {"e2": Pipeline(Ocr(), Rules()), "e3": Pipeline(None, Vision())}
    monkeypatch.setattr(api, "get_pipeline", lambda method: pipelines[method])
    monkeypatch.setattr(api, "choose_method", lambda profile, method, template=None: (None, [method]))
    Ocr.calls, Rules.seen = 0, None

    return TestClient(api.app)


def extract(client, method, **data):
    return client.post("/extract", files={"file": ("f.pdf", text_pdf(INVOICE))}, data={"method": method, **data})


def test_the_rules_read_the_text_layer_without_ocr(client):
    body = extract(client, "e2").json()

    assert Ocr.calls == 0
    assert Rules.seen[2] == "Un 50 kg 1.250,00"
    assert body["fields"]["company"] == "ABC GIDA SANAYI"
    assert body["text_source"] == "pdf"
    assert body["lines"][1]["text"] == "Fatura No: ABC2024000153"


def test_ocr_can_be_asked_for(client):
    body = extract(client, "e2", pdf_text="off").json()

    assert Ocr.calls == 1
    assert body["text_source"] == "ocr"


def test_an_engine_reading_the_image_is_unchanged(client):
    body = extract(client, "e3").json()

    assert body["fields"]["company"] == "read from the image"
    assert body["text_source"] is None


def test_teaching_gets_the_text_layer_s_lines(client):
    body = client.post("/ocr", files={"file": ("f.pdf", text_pdf(INVOICE))}).json()

    assert Ocr.calls == 0
    assert body["text_source"] == "pdf"
    assert [line["text"] for line in body["lines"]][-1] == "Genel Toplam 1.250,00 TL"


def test_a_wrong_pdf_text_value_is_refused(client):
    assert extract(client, "e2", pdf_text="maybe").status_code == 400
