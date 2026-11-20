"""The API for rule sets: teach one from three documents, read with it, and
send what it cannot read to E5 with a warning."""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import api
import catalogue
from extractor.fields.line_items import LineItem, LineItemResult
from extractor.fields.template import TemplateExtractor
from extractor.ocr.base import OcrLine, rect_box
from extractor.pipeline import Pipeline, PipelineResult

FIELDS = ("company", "address", "date", "total")


def page(number, date, total, shop="KEDAI BUKU MAJU SDN BHD"):
    texts = [shop, "NO 1, JALAN SAGU", "81200 JOHOR", "TEL: 07-1234567", f"INVOICE NO {number}",
             f"DATE: {date}", f"PEN 1 x {total}", f"TOTAL RM {total}", "CASH 50.00", "THANK YOU PLEASE COME AGAIN"]
    return [OcrLine(text=text, box=rect_box(0, 20 * i, 200, 20 * i + 15)) for i, text in enumerate(texts)]


# Each test image is told apart by its width.
PAGES = {
    41: page(101, "01/02/2019", "12.50"),
    42: page(102, "03/02/2019", "7.80"),
    43: page(103, "15/03/2019", "21.00"),
    44: page(104, "20/04/2019", "9.90"),
    45: [OcrLine(text=text, box=rect_box(0, 0, 9, 9)) for text in ("PASAR MINI", "RESIT", "JUMLAH 3.00")],
}
CONFIRMED = [
    {"company": "KEDAI BUKU MAJU SDN BHD", "address": "NO 1, JALAN SAGU 81200 JOHOR", "date": d, "total": t}
    for d, t in (("01/02/2019", "12.50"), ("03/02/2019", "7.80"), ("15/03/2019", "21.00"))
]


class Ocr:
    name = "fake"

    def read(self, image):
        return PAGES[image.size[0]]


class E5:
    calls = 0

    def run(self, image):
        E5.calls += 1
        items = LineItemResult(items=[LineItem(name="MILK", amount=None)], needs_review=False)
        return PipelineResult(fields={"company": "PASAR MINI", "address": None, "date": None, "total": "3.00"},
                              lines=[], line_items=items)


class E2:
    ocr = Ocr()


def png(width):
    buffer = io.BytesIO()
    Image.new("RGB", (width, 20), "white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPLATES_DIR", str(tmp_path))
    monkeypatch.setattr(api, "get_pipeline", lambda method: E2() if method == "e2" else E5())
    monkeypatch.setattr(api, "build_template_pipeline",
                        lambda template, doctype: Pipeline(Ocr(), TemplateExtractor(template, doctype)))
    monkeypatch.setattr(catalogue, "engines", lambda: {"e5": {"available": True}})
    api.get_template_pipeline.cache_clear()
    E5.calls = 0

    return TestClient(api.app)


def teach(client, widths=(41, 42, 43), values=CONFIRMED, name="Kedai Buku"):
    return client.post("/templates", data={"name": name, "values": json.dumps(values)},
                       files=[("files", (f"{w}.png", png(w))) for w in widths])


def test_three_documents_teach_a_rule_set_that_is_saved_by_name(client, tmp_path):
    body = teach(client).json()

    assert body["status"] == "saved"
    assert body["file"] == "kedai-buku"
    assert (tmp_path / "kedai-buku.yaml").is_file()
    listed = client.get("/templates").json()
    assert listed["templates"]["kedai-buku"]["examples"] == 3
    assert listed["fallback"] == "e5"
    assert listed["fields"] == list(FIELDS)


def test_a_document_of_the_format_is_read_by_the_rule_set(client):
    teach(client)

    body = client.post("/extract", files={"file": ("d.png", png(44))}, data={"template": "Kedai Buku"}).json()

    assert body["method"] == "template:kedai-buku"
    assert body["template"] == "Kedai Buku"
    assert body["fields"]["total"] == "9.90"
    assert body["fields"]["date"] == "20/04/2019"
    assert body["gate"]["accepted"] is True
    assert body["warning"] is None
    assert E5.calls == 0


def test_a_document_the_rule_set_cannot_read_goes_to_e5_with_a_warning(client):
    teach(client)

    body = client.post("/extract", files={"file": ("d.png", png(45))}, data={"template": "kedai-buku"}).json()

    assert [step["method"] for step in body["chain"]] == ["template:kedai-buku", "e5"]
    assert body["method"] == "e5"
    assert body["fields"]["company"] == "PASAR MINI"
    assert body["warning"].startswith("The rule set did not fit this document (rule set: only 0 of")
    assert "E5 read it instead" in body["warning"]


def test_without_e5_the_rule_sets_result_stands_with_a_warning(client, monkeypatch):
    teach(client)
    monkeypatch.setattr(catalogue, "engines", lambda: {"e5": {"available": False}})

    body = client.post("/extract", files={"file": ("d.png", png(45))}, data={"template": "kedai-buku"}).json()

    assert body["method"] == "template:kedai-buku"
    assert "E5 is not available here" in body["warning"]


def test_examples_that_disagree_save_nothing_and_name_the_misfit(client, tmp_path):
    wrong = [dict(CONFIRMED[0]), dict(CONFIRMED[1], total="99.99"), dict(CONFIRMED[2])]

    body = teach(client, values=wrong).json()

    assert body["status"] == "needs_examples"
    assert "total" in body["unstable"]
    assert body["misfits"] == {"total": 2}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("widths, values, status", [
    ((41, 42), CONFIRMED[:2], 400),          # fewer than three documents
    ((41, 42, 43), CONFIRMED[:2], 400),      # one value object per document
])
def test_bad_requests_are_refused(client, widths, values, status):
    assert teach(client, widths=widths, values=values).status_code == status


def test_a_name_in_use_is_not_overwritten(client):
    teach(client)

    assert teach(client).status_code == 409


def test_an_unknown_rule_set_is_not_found(client):
    response = client.post("/extract", files={"file": ("d.png", png(44))}, data={"template": "nope"})

    assert response.status_code == 404


def test_a_rule_set_can_be_deleted(client):
    teach(client)

    assert client.delete("/templates/kedai-buku").status_code == 200
    assert client.get("/templates").json()["templates"] == {}
    assert client.delete("/templates/kedai-buku").status_code == 404


def test_the_table_names_the_rule_set_and_marks_documents_it_did_not_fit():
    from ui.export import _items_check, result_rows

    gate = {"accepted": True, "reasons": []}
    items = {"items": [], "reasons": ["no total line"], "needs_review": True}
    read = {"fields": {"total": "9.90"}, "gate": gate, "method": "template:kedai-buku", "template": "Kedai Buku",
            "warning": None, "chain": [{"method": "template:kedai-buku"}], "seconds": 1.0, "usd": None,
            "line_items": items}
    sent_on = {**read, "method": "e5", "template": None,
               "warning": "The rule set did not fit this document (...). E5 read it instead: check the result."}

    rows = result_rows([{"file": "a.png", "error": None, "result": read},
                        {"file": "b.png", "error": None, "result": sent_on}])

    assert rows[0]["engine"] == "rule set: Kedai Buku"
    assert rows[0]["check"] == ""
    assert rows[1]["engine"] == "e5"
    assert rows[1]["check"] == "rule set did not fit"
    # The rule set's gate checks fields; the items keep their own reasons.
    assert _items_check(read) == "line items: no total line"
