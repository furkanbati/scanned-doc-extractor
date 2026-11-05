"""OpenAI vision: the model reads the document image and returns the fields as JSON."""

import base64
import io
import os

from PIL import Image

from extractor.ocr.base import OcrLine
from extractor.fields.base import Extraction, Usage
from extractor.fields.prompt import instructions, json_schema, parse_fields
from extractor.schema import DocumentType


def image_data_url(image: Image.Image, quality: int = 90) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)

    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


class OpenAIVisionExtractor:
    name = "openai_vision"
    needs_ocr = False

    def __init__(
        self,
        doctype: DocumentType,
        model: str = "gpt-6-luna",
        reasoning_effort: str | None = "low",
        detail: str = "high",
        client=None,
    ):
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=120, max_retries=6)

        self.client = client
        self.doctype = doctype
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.detail = detail

    def extract(self, image: Image.Image, lines: list[OcrLine]) -> Extraction:
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions(self.doctype)},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url(image), "detail": self.detail},
                        }
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": self.doctype.name,
                    "schema": json_schema(self.doctype),
                    "strict": True,
                },
            },
        }

        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort

        response = self.client.chat.completions.create(**request)
        usage = response.usage

        return Extraction(
            fields=parse_fields(response.choices[0].message.content or "", self.doctype),
            usage=Usage(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            ),
        )
