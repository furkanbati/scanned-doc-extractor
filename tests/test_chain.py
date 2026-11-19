"""Modes that run engines in turn: each one whose review gate rejects the
document hands it to the next (profiles.yaml `chain`)."""

import io
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import api
import catalogue
from extractor.fields.base import Usage
from extractor.fields.line_items import LineItem, LineItemResult
from extractor.ocr.base import OcrLine
from extractor.pipeline import PipelineResult

FIELDS = {"company": "ABC SDN BHD", "address": "NO 1, JALAN SAGU", "date": "25/12/2018", "total": "9.00"}
ADDS_UP = LineItemResult(items=[LineItem(name="TEA", amount=Decimal("9.00"))], total=Decimal("9.00"), needs_review=False)
DOES_NOT = LineItemResult(items=[LineItem(name="TEA", amount=Decimal("9.00"))], total=Decimal("9.00"),
                          needs_review=True, reasons=["no total line"])

SPEC = {
    "order": ["economical"],
    "profiles": {
        "economical": {"label": "Economical", "description": "E2, then E3.", "chain": ["e2", "e3"],
                       "measured": {"receipts": 400, "field_accuracy": 0.8, "seconds_per_document": 9.0,
                                    "usd_per_1000": 0.4, "date": "2026-11-19"}},
        "local": {"label": "Local", "description": "E2, then E5.", "chain": ["e2", "e5"]},
    },
}


class Engine:
    def __init__(self, items, usage=None, error=None):
        self.items, self.usage, self.error, self.calls = items, usage, error, 0

    def run(self, image):
        self.calls += 1
        if self.error:
            raise RuntimeError(self.error)
        return PipelineResult(fields=FIELDS, lines=[OcrLine(text="TOTAL 9.00", box=(0, 0, 9, 0, 9, 5, 0, 5))],
                              usage=self.usage, line_items=self.items)


@pytest.fixture
def engines(monkeypatch):
    built = {"e2": Engine(ADDS_UP), "e3": Engine(ADDS_UP, Usage(input_tokens=2000, output_tokens=400))}
    monkeypatch.setattr(api, "get_pipeline", lambda method: built[method])
    monkeypatch.setattr(catalogue, "load_profiles", lambda path=None: SPEC)
    monkeypatch.setattr(catalogue, "ollama_models", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.delenv("DEFAULT_PROFILE", raising=False)

    return built


@pytest.fixture
def client(engines):
    return TestClient(api.app)


def extract(client, mode="economical"):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")

    return client.post("/extract", files={"file": ("r.png", buffer.getvalue())}, data={"profile": mode})


def test_a_document_the_first_engine_accepts_stops_there(client, engines):
    body = extract(client).json()

    assert body["method"] == "e2"
    assert [step["method"] for step in body["chain"]] == ["e2"]
    assert body["chain"][0]["accepted"] is True
    assert engines["e3"].calls == 0
    assert body["usd"] is None


def test_a_rejected_document_goes_to_the_next_engine(client, engines):
    engines["e2"].items = DOES_NOT

    body = extract(client).json()

    assert body["method"] == "e3"
    assert [(step["method"], step["accepted"]) for step in body["chain"]] == [("e2", False), ("e3", True)]
    assert body["chain"][0]["reasons"] == ["line items: no total line"]
    # e3.yaml prices: (2000 * 0.10 + 400 * 0.50) / 1e6, paid only for E3
    assert body["usd"] == pytest.approx(0.0004)
    assert body["seconds"] == pytest.approx(sum(step["seconds"] for step in body["chain"]), abs=0.002)


def test_the_last_engine_answers_even_when_its_checks_fail(client, engines):
    engines["e2"].items = DOES_NOT
    engines["e3"].items = DOES_NOT

    body = extract(client).json()

    assert body["method"] == "e3"
    assert body["gate"]["accepted"] is False


def test_when_the_next_engine_fails_the_earlier_result_stands(client, engines):
    engines["e2"].items = DOES_NOT
    engines["e3"].error = "server error"

    response = extract(client)

    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "e2"
    assert body["chain"][1] == {"method": "e3", "seconds": None, "usd": None, "accepted": None,
                                "reasons": [], "error": "server error"}


def test_when_every_engine_fails_the_request_fails(client, engines):
    engines["e2"].error = "OCR crashed"

    response = extract(client, "local")

    assert response.status_code == 502
    assert "OCR crashed" in response.json()["detail"]


def test_an_engine_that_cannot_run_is_left_out_of_the_chain(client):
    local = client.get("/profiles").json()["profiles"]["local"]

    assert local["chain"] == ["e2"]
    assert "Ollama" in local["skipped"]["e5"]
    assert local["available"] is True
    assert local["leaves_machine"] is False


def test_a_chain_with_a_cloud_engine_may_send_the_document_out(client):
    economical = client.get("/profiles").json()["profiles"]["economical"]

    assert economical["chain"] == ["e2", "e3"]
    assert economical["leaves_machine"] is True
    assert economical["line_items"] is True
    assert economical["measured"]["usd_per_1000"] == 0.4


def test_a_chain_none_of_whose_engines_can_run_is_unavailable(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    spec = {"order": ["cloud"], "profiles": {"cloud": {"label": "Cloud", "description": "E3, then E5.", "chain": ["e3", "e5"]}}}
    monkeypatch.setattr(catalogue, "load_profiles", lambda path=None: spec)

    cloud = client.get("/profiles").json()["profiles"]["cloud"]

    assert cloud["available"] is False
    assert extract(client, "cloud").status_code == 503


def test_an_engine_without_line_items_cannot_be_checked_so_the_next_one_runs(client, engines):
    engines["e2"].items = None

    body = extract(client).json()

    assert [step["method"] for step in body["chain"]] == ["e2", "e3"]
    assert body["method"] == "e3"


def test_a_single_engine_request_has_a_one_step_chain(client):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")

    body = client.post("/extract", files={"file": ("r.png", buffer.getvalue())}, data={"method": "e2"}).json()

    assert [step["method"] for step in body["chain"]] == ["e2"]
    assert json.dumps(body)  # serializable


def test_the_table_names_the_engine_that_answered():
    from ui.export import result_rows

    rows = result_rows([{"file": "a.png", "error": None,
                         "result": {"fields": FIELDS, "gate": {"accepted": True, "reasons": []}, "method": "e3",
                                    "chain": [{"method": "e2"}, {"method": "e3"}], "seconds": 1.0, "usd": 0.0004}}])

    assert rows[0]["engine"] == "e3"


def test_the_sroie_chain_takes_e2_where_its_gate_accepted():
    from evaluation.sroie_chain import chain

    def row(accepted, *right):
        return {"accepted": accepted, "seconds": 2.0,
                "matches": {field: field in right for field in ("company", "address", "date", "total")}}

    first = {"a": row(True, "date", "total"), "b": row(False)}
    second = {"a": row(None, "company", "address", "date", "total"), "b": row(None, "company", "date", "total")}

    summary = chain(first, second, {"seconds_per_receipt": 10.0, "usd_per_receipt": 0.0005})

    assert (summary["kept_by_e2"], summary["sent_on"]) == (1, 1)
    assert summary["field_accuracy"]["mean"] == (2 + 3) / 8
    assert summary["second_alone"]["mean"] == (4 + 3) / 8
    assert summary["seconds_per_receipt"] == 2.0 + 10.0 / 2
    assert summary["usd_per_1000"] == 0.0005 / 2 * 1000
