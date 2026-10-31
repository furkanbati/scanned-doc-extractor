import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import api
from extractor.dataset import OcrLine
from extractor.fields.base import Usage
from extractor.locate import locate_fields
from extractor.pipeline import PipelineResult


def line(text):
    return OcrLine(text=text, box=(0, 0, 10, 0, 10, 5, 0, 5))


class FakePipeline:
    def run(self, image):
        return PipelineResult(
            fields={"company": "ABC SDN BHD", "date": "25/12/2018", "total": "RM 9.00"},
            lines=[line("ABC SDN BHD"), line("NO 1, JALAN SAGU"), line("TOTAL RM 9.00")],
            usage=Usage(input_tokens=2000, output_tokens=100),
        )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "get_pipeline", lambda method: FakePipeline())

    return TestClient(api.app)


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")

    return buffer.getvalue()


def test_methods_lists_the_configs(client):
    body = client.get("/methods").json()

    assert {"e1", "e2", "e3"} <= set(body["methods"])
    assert body["methods"]["e3"]["leaves_machine"] is True
    assert body["methods"]["e2"]["leaves_machine"] is False


def test_extract_returns_fields_boxes_and_cost(client):
    response = client.post("/extract", files={"file": ("r.png", png_bytes())}, data={"method": "e3"})

    body = response.json()
    assert response.status_code == 200
    assert body["fields"]["company"] == "ABC SDN BHD"
    assert body["fields"]["address"] is None
    assert body["normalized"]["date"] == "2018-12-25"
    assert body["normalized"]["total"] == "9.00"
    assert body["field_lines"]["company"] == [0]
    assert body["field_lines"]["total"] == [2]
    # e3 prices: (2000 * 0.10 + 100 * 0.50) / 1e6
    assert body["usd"] == pytest.approx(0.00025)
    assert body["image_size"] == [40, 20]


def test_extract_rejects_unknown_method(client):
    response = client.post("/extract", files={"file": ("r.png", png_bytes())}, data={"method": "nope"})

    assert response.status_code == 400


def test_extract_rejects_non_images(client):
    response = client.post("/extract", files={"file": ("r.txt", b"not an image")}, data={"method": "e2"})

    assert response.status_code == 400


def test_default_method_depends_on_the_openai_key(monkeypatch):
    monkeypatch.delenv("DEFAULT_METHOD", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert api.default_method() == "e2"

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    assert api.default_method() == "e3"


def test_locate_fields():
    lines = [line("ABC SDN BHD"), line("NO 1, JALAN SAGU,"), line("81100 JOHOR"), line("RM"), line("TOTAL 9.00")]

    located = locate_fields(
        {"company": "ABC SDN BHD", "address": "NO 1, JALAN SAGU, 81100 JOHOR", "total": "9.00", "date": None},
        lines,
    )

    assert located == {"company": [0], "address": [1, 2], "total": [4], "date": []}
