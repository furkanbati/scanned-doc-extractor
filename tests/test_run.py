import json

import pytest
from PIL import Image

from evaluation import run
from extractor.dataset import OcrLine, load_sroie
from extractor.pipeline import Pipeline, build_pipeline
from extractor.fields.rules import extract_fields


class FakeOcr:
    """Returns the receipt's lines; picks them by image width (the receipt id)."""

    name = "fake"

    def __init__(self, lines_by_width):
        self.lines_by_width = lines_by_width

    def read(self, image):
        return self.lines_by_width[image.width]

    def version(self):
        return "0"


@pytest.fixture
def sroie_root(tmp_path):
    for sub in ("img", "key", "box"):
        (tmp_path / sub).mkdir()

    receipts = {
        "000": ({"company": "ABC SDN BHD", "date": "25/12/2018", "total": "9.00"},
                ["ABC SDN BHD", "DATE 25/12/2018", "TOTAL 9.00"]),
        "001": ({"company": "XYZ TRADING", "date": "01/01/2019", "total": "5.00"},
                ["XYZ TRADING", "DATE 01/01/2019", "TOTAL 5.00"]),
    }

    for number, (sample_id, (key, lines)) in enumerate(receipts.items()):
        Image.new("RGB", (10 + number, 10), "white").save(tmp_path / "img" / f"{sample_id}.jpg")
        (tmp_path / "key" / f"{sample_id}.json").write_text(json.dumps(key))
        (tmp_path / "box" / f"{sample_id}.csv").write_text(
            "".join(f"0,0,1,0,1,1,0,1,{line}\n" for line in lines)
        )

    return tmp_path


def test_run_and_write_reports(sroie_root, tmp_path):
    samples = load_sroie(sroie_root, split="all")
    ocr = FakeOcr({
        10: samples[0].lines,
        # Second receipt: wrong total, one character off in the OCR text.
        11: [OcrLine("XYZ TRADING", (0,) * 8), OcrLine("DATE 01/01/2019", (0,) * 8),
             OcrLine("TOTAL 6.00", (0,) * 8)],
    })

    results = run.run_samples(Pipeline(ocr, extract_fields), samples)
    summary = run.summarize(results, run.label_line_accuracy({"fields": "rules"}, samples))

    assert summary["receipts"] == 2
    assert summary["field_accuracy"]["total"] == 0.5
    assert summary["field_accuracy"]["date"] == 1.0
    assert summary["fields_from_labelled_lines"]["total"] == 1.0
    assert summary["cer"] == pytest.approx(1 / sum(len(s.text) for s in samples))

    output = tmp_path / "out"
    run.write_reports(output, summary, {"config": "e1", "description": "test"},
                      run.per_receipt_rows(results))

    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    rows = (output / "receipts.jsonl").read_text(encoding="utf-8").splitlines()

    assert report["field_accuracy"]["total"] == 0.5
    assert json.loads(rows[1])["matches"]["total"] is False
    assert "| OCR |" in (output / "report.md").read_text(encoding="utf-8")


def test_load_config():
    config = run.load_config("e1")

    assert config["name"] == "e1"
    assert build_pipeline(config).ocr.name == "tesseract"


def test_build_pipeline_rejects_unknown_stages():
    with pytest.raises(ValueError):
        build_pipeline({"preprocess": "opencv", "ocr": "tesseract", "fields": "rules"})

    with pytest.raises(ValueError):
        build_pipeline({"ocr": "paddle", "fields": "rules"})

    with pytest.raises(ValueError):
        build_pipeline({"ocr": "tesseract", "fields": "llm"})
