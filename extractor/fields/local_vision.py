"""Local vision model (Ollama): the model reads the document image itself.

No OCR, and the document never leaves the machine.
"""

import io
import os

from PIL import Image

from extractor.ocr.base import OcrLine
from extractor.fields.base import Extraction, Usage
from extractor.fields.local_llm import DEFAULT_HOST, chat_with_retry
from extractor.fields.prompt import instructions, json_schema, parse_fields
from extractor.schema import DocumentType


def jpeg_bytes(image: Image.Image, max_side: int | None = None, quality: int = 90) -> bytes:
    image = image.convert("RGB")
    if max_side and max(image.size) > max_side:
        image.thumbnail((max_side, max_side))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)

    return buffer.getvalue()


class LocalVisionExtractor:
    name = "local_vision"
    needs_ocr = False

    def __init__(
        self,
        doctype: DocumentType,
        model: str = "qwen3-vl:2b",
        think: bool = False,
        max_tokens: int = 512,
        max_side: int | None = 1600,
        host: str | None = None,
        client=None,
    ):
        if client is None:
            from ollama import Client

            client = Client(host=host or os.getenv("OLLAMA_HOST", DEFAULT_HOST), timeout=900)

        self.client = client
        self.doctype = doctype
        self.model = model
        self.think = think
        self.max_tokens = max_tokens
        # Larger images cost more vision tokens and time on CPU.
        self.max_side = max_side

    def extract(self, image: Image.Image, lines: list[OcrLine]) -> Extraction:
        response = chat_with_retry(
            self.client,
            model=self.model,
            messages=[
                {"role": "system", "content": instructions(self.doctype)},
                {
                    "role": "user",
                    "content": "Extract the fields from this document image.",
                    "images": [jpeg_bytes(image, self.max_side)],
                },
            ],
            format=json_schema(self.doctype),
            think=self.think,
            options={"temperature": 0, "num_predict": self.max_tokens},
        )

        return Extraction(
            fields=parse_fields(response.message.content or "", self.doctype),
            usage=Usage(
                input_tokens=response.prompt_eval_count or 0,
                output_tokens=response.eval_count or 0,
            ),
        )
