import io

import pypdfium2
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import api
import catalogue
from extractor.ocr.base import OcrLine
from extractor.fields.base import Usage
from extractor.locate import locate_fields
from extractor.pipeline import PipelineResult


def line(text):
    return OcrLine(text=text, box=(0, 0, 10, 0, 10, 5, 0, 5))


class FakePipeline:
    def __init__(self):
        self.images = []

    def run(self, image):
        self.images.append(image)
        return PipelineResult(
            fields={"company": "ABC SDN BHD", "date": "25/12/2018", "total": "RM 9.00"},
            lines=[line("ABC SDN BHD"), line("NO 1, JALAN SAGU"), line("TOTAL RM 9.00")],
            usage=Usage(input_tokens=2000, output_tokens=100),
        )


@pytest.fixture
def pipeline(monkeypatch):
    fake = FakePipeline()
    monkeypatch.setattr(api, "get_pipeline", lambda method: fake)
    monkeypatch.setattr(catalogue, "ollama_reachable", lambda: False)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.delenv("DEFAULT_PROFILE", raising=False)

    return fake


@pytest.fixture
def client(pipeline):
    return TestClient(api.app)


def png_bytes(size=(40, 20)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")

    return buffer.getvalue()


def pdf_bytes(pages=2):
    pdf = pypdfium2.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(144, 72)  # 2 x 1 inch
    buffer = io.BytesIO()
    pdf.save(buffer)

    return buffer.getvalue()


def test_profiles_carry_engine_and_measured_numbers(client):
    body = client.get("/profiles").json()

    assert body["default"] == "accurate"
    accurate = body["profiles"]["accurate"]
    assert accurate["method"] == "e3"
    assert accurate["leaves_machine"] is True
    assert accurate["available"] is True
    assert accurate["measured"]["receipts"] == 400
    assert accurate["measured"]["field_accuracy"] == pytest.approx(0.7975)
    assert body["profiles"]["private"]["leaves_machine"] is False
    assert "Google Gemini" in body["planned"]


def test_without_a_key_the_private_mode_is_the_default(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")

    body = client.get("/profiles").json()

    assert body["default"] == "private"
    assert body["profiles"]["accurate"]["available"] is False
    assert "OPENAI_API_KEY" in body["profiles"]["accurate"]["unavailable_reason"]


def test_methods_list_every_engine_with_availability(client):
    engines = client.get("/methods").json()["methods"]

    assert {"e1", "e2", "e3", "e4", "e5"} <= set(engines)
    assert engines["e2"]["available"] is True
    assert engines["e4"]["available"] is False  # no Ollama in the test
    assert engines["e3"]["model"] == "gpt-6-luna"
    assert engines["e4"]["measured"] is None


def test_extract_with_a_mode(client):
    response = client.post("/extract", files={"file": ("r.png", png_bytes())}, data={"profile": "accurate"})

    body = response.json()
    assert response.status_code == 200
    assert body["profile"] == "accurate"
    assert body["method"] == "e3"
    assert body["fields"]["company"] == "ABC SDN BHD"
    assert body["fields"]["address"] is None
    assert body["normalized"]["date"] == "2018-12-25"
    assert body["normalized"]["total"] == "9.00"
    assert body["review"] == {"address": "not found"}
    assert body["field_lines"]["company"] == [0]
    assert body["field_lines"]["total"] == [2]
    # e3 prices: (2000 * 0.10 + 100 * 0.50) / 1e6
    assert body["usd"] == pytest.approx(0.00025)
    assert body["image_size"] == [40, 20]
    assert body["pages"] == 1


def test_extract_uses_the_default_mode(client):
    body = client.post("/extract", files={"file": ("r.png", png_bytes())}).json()

    assert body["profile"] == "accurate"


def test_extract_with_an_engine(client):
    body = client.post("/extract", files={"file": ("r.png", png_bytes())}, data={"method": "e2"}).json()

    assert body["profile"] is None
    assert body["method"] == "e2"
    assert body["usd"] is None  # e2 has no prices


def test_extract_reads_the_first_pdf_page(client, pipeline):
    body = client.post("/extract", files={"file": ("r.pdf", pdf_bytes(pages=2))}).json()

    assert body["pages"] == 2
    # 2 x 1 inch at 200 dpi
    assert body["image_size"] == [400, 200]
    assert pipeline.images[0].size == (400, 200)


def test_extract_refuses_an_engine_that_cannot_run_here(client):
    response = client.post("/extract", files={"file": ("r.png", png_bytes())}, data={"method": "e4"})

    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]


def test_extract_refuses_a_mode_without_its_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")

    response = client.post("/extract", files={"file": ("r.png", png_bytes())}, data={"profile": "accurate"})

    assert response.status_code == 503


@pytest.mark.parametrize("data", [{"method": "nope"}, {"profile": "nope"}])
def test_extract_rejects_unknown_names(client, data):
    response = client.post("/extract", files={"file": ("r.png", png_bytes())}, data=data)

    assert response.status_code == 400


def test_extract_rejects_files_that_are_not_documents(client):
    response = client.post("/extract", files={"file": ("r.txt", b"not an image")})

    assert response.status_code == 400


def test_extract_rejects_a_broken_pdf(client):
    response = client.post("/extract", files={"file": ("r.pdf", b"%PDF-1.7 broken")})

    assert response.status_code == 400


def test_extract_reports_a_failing_engine(client, monkeypatch):
    class Broken:
        def run(self, image):
            raise RuntimeError("timed out")

    monkeypatch.setattr(api, "get_pipeline", lambda method: Broken())

    response = client.post("/extract", files={"file": ("r.png", png_bytes())})

    assert response.status_code == 502
    assert "timed out" in response.json()["detail"]


def test_default_profile_can_be_set(client, monkeypatch):
    monkeypatch.setenv("DEFAULT_PROFILE", "private")

    assert client.get("/profiles").json()["default"] == "private"


def test_locate_fields():
    lines = [line("ABC SDN BHD"), line("NO 1, JALAN SAGU,"), line("81100 JOHOR"), line("RM"), line("TOTAL 9.00")]

    located = locate_fields(
        {"company": "ABC SDN BHD", "address": "NO 1, JALAN SAGU, 81100 JOHOR", "total": "9.00", "date": None},
        lines,
    )

    assert located == {"company": [0], "address": [1, 2], "total": [4], "date": []}


def test_extract_without_line_items_says_so(client):
    body = client.post("/extract", files={"file": ("r.png", png_bytes())}).json()

    assert body["line_items"] is None
    assert body["gate"] is None


def test_extract_returns_line_items(client, monkeypatch):
    from decimal import Decimal

    from extractor.fields.line_items import LineItem, LineItemResult

    class WithItems:
        def run(self, image):
            return PipelineResult(
                fields={"total": "57.900"},
                lines=[line("TOTAL 57.900")],
                line_items=LineItemResult(
                    items=[
                        LineItem(name="ISI CAMPUR", amount=Decimal(43636), quantity=Decimal(1), unit_price=Decimal(43636)),
                        LineItem(name="AQUA BOTOL", amount=Decimal(9000)),
                    ],
                    subtotal=Decimal(52636),
                    tax=Decimal(5264),
                    total=Decimal(57900),
                    needs_review=True,
                    reasons=["total found by arithmetic, not by a TOTAL line"],
                ),
            )

    monkeypatch.setattr(api, "get_pipeline", lambda method: WithItems())

    body = client.post("/extract", files={"file": ("r.png", png_bytes())}, data={"method": "e2"}).json()
    items = body["line_items"]

    assert [item["name"] for item in items["items"]] == ["ISI CAMPUR", "AQUA BOTOL"]
    assert items["items"][0] == {"name": "ISI CAMPUR", "amount": "43636", "quantity": "1", "unit_price": "43636"}
    assert items["items"][1]["quantity"] is None
    assert items["subtotal"] == "52636" and items["tax"] == "5264" and items["total"] == "57900"
    assert items["needs_review"] is True
    assert items["reasons"] == ["total found by arithmetic, not by a TOTAL line"]
    # Field notes and line-item reasons, as the export's check column shows them.
    assert body["gate"] == {
        "accepted": False,
        "reasons": [
            "company not found",
            "address not found",
            "date not found",
            "line items: total found by arithmetic, not by a TOTAL line",
        ],
    }


def test_methods_and_modes_say_which_read_line_items(client):
    engines = client.get("/methods").json()["methods"]
    profiles = client.get("/profiles").json()["profiles"]

    assert engines["e2"]["line_items"] is True
    assert engines["e3"]["line_items"] is False
    assert profiles["private"]["line_items"] is True
    assert profiles["accurate"]["line_items"] is False


def test_extract_checks_a_total_that_differs_from_the_line_items(client, monkeypatch):
    from decimal import Decimal

    from extractor.fields.line_items import LineItem, LineItemResult

    def pipeline_with(total):
        class Pipeline:
            def run(self, image):
                return PipelineResult(
                    fields={"company": "ABC SDN BHD", "address": "NO 1", "date": "25/12/2018", "total": total},
                    lines=[line("TOTAL")],
                    line_items=LineItemResult(
                        items=[LineItem(name="TEA", amount=Decimal(57900))],
                        total=Decimal(57900),
                        needs_review=False,
                    ),
                )

        return Pipeline()

    monkeypatch.setattr(api, "get_pipeline", lambda method: pipeline_with("10.00"))
    body = client.post("/extract", files={"file": ("r.png", png_bytes())}, data={"method": "e2"}).json()
    assert body["review"] == {"total": "differs from the line items' total 57900"}
    assert body["gate"] == {"accepted": False, "reasons": ["total differs from the line items' total 57900"]}

    monkeypatch.setattr(api, "get_pipeline", lambda method: pipeline_with("57.900"))
    body = client.post("/extract", files={"file": ("r.png", png_bytes())}, data={"method": "e2"}).json()
    assert body["review"] == {}
    assert body["gate"] == {"accepted": True, "reasons": []}
