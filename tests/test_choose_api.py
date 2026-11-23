"""template=auto: the API chooses the rule set by the document's fingerprint."""

import pytest

import api
from tests.test_doctypes_api import E2, E5, Ocr, client, png, teach  # noqa: F401  (client is a fixture)


@pytest.fixture
def e5_too(client, monkeypatch):
    """The chosen mode's E5 reads documents of no known format."""

    monkeypatch.setattr(api, "get_pipeline", lambda method: E2() if method == "e2" else E5("receipt"))

    return client


def test_a_document_of_a_known_format_is_read_with_its_rule_set(client):
    teach(client)
    calls = []
    original = Ocr.read
    Ocr.read = lambda self, image: calls.append(1) or original(self, image)
    try:
        body = client.post("/extract", files={"file": ("n.png", png(44))},
                           data={"template": "auto", "method": "e5"}).json()
    finally:
        Ocr.read = original

    assert body["format"]["rule_set"] == "abc-gida"
    assert body["format"]["found"] >= 0.6 * body["format"]["fixed"]
    assert body["method"] == "template:abc-gida"
    assert body["fields"]["invoice_number"] == "A-2024-0171"
    assert body["gate"]["accepted"] is True
    # The lines were read once, to choose and to read.
    assert len(calls) == 1
    assert body["text_source"] == "ocr"


def test_a_document_of_no_known_format_goes_to_the_chosen_mode(e5_too):
    client = e5_too
    teach(client)

    body = client.post("/extract", files={"file": ("o.png", png(45))},
                       data={"template": "auto", "method": "e5"}).json()

    assert body["format"] is None
    assert body["method"] == "e5"
    assert body["warning"] is None


def test_without_rule_sets_auto_reads_with_the_chosen_mode(e5_too):
    client = e5_too
    body = client.post("/extract", files={"file": ("n.png", png(44))},
                       data={"template": "auto", "method": "e5"}).json()

    assert body["format"] is None
    assert body["method"] == "e5"
    assert E5.doctypes == ["receipt"]
