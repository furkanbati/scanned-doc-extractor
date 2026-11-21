"""/extract reads an e-Fatura's XML itself, whatever mode was chosen."""

import pytest
from fastapi.testclient import TestClient

import api
from tests.test_efatura import invoice


@pytest.fixture
def client(monkeypatch):
    def no_engine(*args, **kwargs):
        raise AssertionError("an XML invoice needs no engine")

    monkeypatch.setattr(api, "run_engine", no_engine)

    return TestClient(api.app)


def test_an_xml_invoice_is_read_from_its_tags(client):
    body = client.post("/extract", files={"file": ("f.xml", invoice())}, data={"method": "e5"}).json()

    assert body["method"] == "efatura_xml"
    assert body["doctype"] == "invoice"
    assert body["fields"]["invoice_number"] == "ABC2024000000153"
    assert body["normalized"]["total"] == "2676.60"
    assert [item["name"] for item in body["line_items"]["items"]] == ["Un 50 kg", "Şeker 25 kg"]
    assert body["meta"]["ettn"] == "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    assert body["gate"] == {"accepted": True, "reasons": []}
    assert body["chain"][0]["method"] == "efatura_xml"
    assert body["usd"] is None


def test_an_invoice_whose_amounts_do_not_add_up_needs_a_look(client):
    body = client.post("/extract", files={"file": ("f.xml", invoice(tax="400.00"))}).json()

    assert body["gate"]["accepted"] is False
    assert body["gate"]["reasons"] == ["line items: amount before tax plus tax is not the amount with tax"]


def test_other_xml_is_refused(client):
    response = client.post("/extract", files={"file": ("n.xml", b"<note/>")})

    assert response.status_code == 400
    assert "Not a UBL invoice" in response.json()["detail"]
