import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PIL import Image

from extractor.fields.model_items import SCHEMA, parse_line_items, with_line_items
from extractor.fields.prompt import json_schema
from extractor.pipeline import build_pipeline, load_engine, reads_line_items
from extractor.schema import load_doctype

RECEIPT = load_doctype("receipt")


def answer(items, **sums):
    return json.dumps(
        {
            "company": None,
            "total": sums.get("total"),
            "line_items": {
                "items": [{"name": n, "quantity": q, "amount": a, "add_ons": x} for n, q, a, x in items],
                **{k: sums.get(k) for k in ("subtotal", "tax", "service", "discount", "total")},
            },
        }
    )


def test_schema_adds_a_required_line_items_object():
    schema = with_line_items(json_schema(RECEIPT))

    assert schema["required"] == [*RECEIPT.field_names, "line_items"]
    assert schema["properties"]["line_items"] is SCHEMA
    assert SCHEMA["properties"]["items"]["items"]["required"] == ["name", "quantity", "amount", "add_ons"]


def test_items_that_add_up_need_no_review():
    result = parse_line_items(answer(
        [("NASI GORENG", "1", "25.000", None), ("ES TEH", "2", "10.000", None)],
        subtotal="35.000", tax="3.500", total="38.500",
    ))

    assert result.amounts == [Decimal(25000), Decimal(10000)]
    assert result.items[1].quantity == Decimal(2)
    assert (result.subtotal, result.tax, result.total) == (Decimal(35000), Decimal(3500), Decimal(38500))
    assert not result.needs_review


def test_numbers_that_do_not_add_up_are_flagged_like_the_rules():
    result = parse_line_items(answer([("CAKE", None, "9,000", None)], total="12,000"))

    assert result.needs_review
    assert result.reasons == ["expected total 9000, found 12000"]


def test_add_ons_count_towards_the_sums_but_are_not_items():
    result = parse_line_items(answer([("LEMON TEA", None, "25,000", "5,000")], total="30,000"))

    assert result.amounts == [Decimal(25000)]
    assert result.reasons == ["add-on lines were added to their item"]
    assert result.needs_review


def test_an_item_without_an_amount_is_left_out_and_flagged():
    result = parse_line_items(answer([("TEA", None, "9,000", None), ("NOTE", None, None, None)], total="9,000"))

    assert result.amounts == [Decimal(9000)]
    assert result.reasons == ["1 item(s) without an amount"]


def test_zero_sums_the_model_fills_in_change_nothing():
    result = parse_line_items(answer([("TEA", None, "9,000", None)], tax="0", service="0", discount="0", total="9,000"))

    assert not result.needs_review


@pytest.mark.parametrize("content", ["not json", "[1]", json.dumps({"company": "X"}), json.dumps({"line_items": []})])
def test_unreadable_answers_need_review(content):
    result = parse_line_items(content)

    assert result.items == []
    assert result.needs_review


class FakeOllama:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, **call):
        self.calls.append(call)
        return SimpleNamespace(message=SimpleNamespace(content=self.content), prompt_eval_count=1500, eval_count=200)


def test_local_vision_asks_for_line_items_when_told():
    from extractor.fields.local_vision import LocalVisionExtractor

    client = FakeOllama(answer([("TEA", None, "9,000", None)], total="9,000"))
    extractor = LocalVisionExtractor(RECEIPT, line_items=True, threads=8, client=client)

    extraction = extractor.extract(Image.new("RGB", (40, 20)), [])

    call = client.calls[0]
    assert "line_items" in call["format"]["properties"]
    assert "add_ons" in call["messages"][0]["content"]
    assert call["options"]["num_thread"] == 8
    assert extraction.line_items.amounts == [Decimal(9000)]
    assert extraction.fields["total"] == "9,000"


def test_local_vision_without_line_items_is_unchanged():
    from extractor.fields.local_vision import LocalVisionExtractor

    client = FakeOllama(json.dumps({"total": "9.00"}))
    extraction = LocalVisionExtractor(RECEIPT, client=client).extract(Image.new("RGB", (40, 20)), [])

    assert client.calls[0]["format"] == json_schema(RECEIPT)
    assert "num_thread" not in client.calls[0]["options"]
    assert extraction.line_items is None


def test_e5_reads_line_items_with_the_model(monkeypatch):
    import extractor.pipeline as pipeline

    built = {}
    monkeypatch.setattr(pipeline, "build_extractor", lambda name, doctype, options=None: built.update(options) or SimpleNamespace(name=name, needs_ocr=False))

    config = load_engine("e5")
    result = build_pipeline(config, RECEIPT)

    assert reads_line_items(config, RECEIPT)
    assert built["line_items"] is True
    # The pipeline does not run the rules: the model returns the items.
    assert result.line_items is False


def test_line_items_by_model_need_a_model_that_can_read_them():
    config = {**load_engine("e2"), "line_items": "model"}

    with pytest.raises(ValueError, match="cannot read line items"):
        build_pipeline(config, RECEIPT)
