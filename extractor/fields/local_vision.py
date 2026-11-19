"""Local vision model (Ollama): the model reads the document image itself.

No OCR, and the document never leaves the machine.
"""

import io
import os

from PIL import Image

from extractor.ocr.base import OcrLine
from extractor.fields.base import Extraction, Usage
from extractor.fields.local_llm import DEFAULT_HOST, chat_with_retry
from extractor.fields.model_items import INSTRUCTIONS as ITEM_INSTRUCTIONS
from extractor.fields.model_items import parse_line_items, with_line_items
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
        model: str = "qwen3-vl:2b-instruct",
        think: bool = False,
        max_tokens: int = 512,
        max_side: int | None = 1600,
        line_items: bool = False,
        threads: int | None = None,
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
        # Also ask for the purchased items and the sums below them.
        self.line_items = line_items
        # CPU threads for Ollama; its default (every core) can be far slower
        # where the cores are shared, as in a Docker VM on a laptop.
        self.threads = threads

    def answer(self, image: Image.Image) -> str:
        """The model's raw JSON answer (for the CORD evaluation's cache)."""

        return self._chat(image).message.content or ""

    def extract(self, image: Image.Image, lines: list[OcrLine]) -> Extraction:
        response = self._chat(image)
        content = response.message.content or ""

        return Extraction(
            fields=parse_fields(content, self.doctype),
            line_items=parse_line_items(content) if self.line_items else None,
            usage=Usage(
                input_tokens=response.prompt_eval_count or 0,
                output_tokens=response.eval_count or 0,
            ),
        )

    def _chat(self, image: Image.Image):
        system, schema = instructions(self.doctype), json_schema(self.doctype)
        if self.line_items:
            system, schema = system + ITEM_INSTRUCTIONS, with_line_items(schema)

        return chat_with_retry(
            self.client,
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": "Extract the fields from this document image.",
                    "images": [jpeg_bytes(image, self.max_side)],
                },
            ],
            format=schema,
            think=self.think,
            options={
                "temperature": 0,
                "num_predict": self.max_tokens,
                **({"num_thread": self.threads} if self.threads else {}),
            },
        )
