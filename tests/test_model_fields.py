import json
from types import SimpleNamespace

from PIL import Image

from extractor.fields.openai_vision import OpenAIVisionExtractor, image_data_url
from extractor.fields.prompt import instructions, json_schema, parse_fields
from extractor.schema import load_doctype

RECEIPT = load_doctype("receipt")


def test_instructions_list_every_field_with_its_description():
    text = instructions(RECEIPT)

    for field in RECEIPT.fields:
        assert f"- {field.name} ({field.type}): {field.description}" in text


def test_json_schema_is_strict():
    schema = json_schema(RECEIPT)

    assert schema["required"] == RECEIPT.field_names
    assert schema["additionalProperties"] is False
    assert schema["properties"]["total"]["type"] == ["string", "null"]


def test_parse_fields():
    content = json.dumps({"company": "ABC", "total": 9.5, "date": None, "extra": "x"})

    assert parse_fields(content, RECEIPT) == {
        "company": "ABC",
        "address": None,
        "date": None,
        "total": "9.5",
    }


def test_parse_fields_survives_bad_json():
    assert parse_fields("not json", RECEIPT) == RECEIPT.values({})
    assert parse_fields("[1, 2]", RECEIPT) == RECEIPT.values({})


def test_image_data_url_is_jpeg():
    assert image_data_url(Image.new("RGB", (4, 4))).startswith("data:image/jpeg;base64,")


class FakeCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "company": "ABC SDN BHD", "address": None, "date": "25/12/2018", "total": "9.00",
            })))],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=150),
        )


def test_openai_vision_request_and_result():
    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    extractor = OpenAIVisionExtractor(RECEIPT, model="gpt-6-luna", client=client)

    extraction = extractor.extract(Image.new("RGB", (8, 8), "white"), [])

    request = completions.requests[0]
    assert request["model"] == "gpt-6-luna"
    assert request["reasoning_effort"] == "low"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/jpeg")
    assert extraction.fields["total"] == "9.00"
    assert extraction.usage.input_tokens == 1200
    assert extraction.usage.output_tokens == 150
