"""Local LLM (Ollama): the model reads the OCR text and returns the fields as JSON.

The document never leaves the machine.
"""

import os
import time

from extractor.ocr.base import OcrLine
from extractor.fields.base import Extraction, Usage
from extractor.fields.prompt import instructions, json_schema, parse_fields
from extractor.ocr.base import lines_to_text
from extractor.schema import DocumentType

DEFAULT_HOST = "http://ollama:11434"


def chat_with_retry(client, attempts: int = 3, wait: float = 5.0, **call):
    """Ollama restarts a model runner that crashed (out of memory, say); a
    server error is retried after a pause instead of losing the document."""

    for attempt in range(1, attempts + 1):
        try:
            return client.chat(**call)
        except Exception as error:
            server_error = getattr(error, "status_code", 0) >= 500
            if not server_error or attempt == attempts:
                raise

            print(f"Ollama error, retry {attempt}/{attempts - 1}: {error}", flush=True)
            time.sleep(wait)


class LocalLLMExtractor:
    name = "local_llm"
    needs_ocr = True

    def __init__(
        self,
        doctype: DocumentType,
        model: str = "qwen3.5:4b",
        think: bool = False,
        max_tokens: int = 512,
        context: int = 3072,
        host: str | None = None,
        client=None,
    ):
        if client is None:
            from ollama import Client

            client = Client(host=host or os.getenv("OLLAMA_HOST", DEFAULT_HOST), timeout=600)

        self.client = client
        self.doctype = doctype
        self.model = model
        self.think = think
        # Small models can loop under a JSON format; a cap ends such a reply.
        self.max_tokens = max_tokens
        # A receipt prompt is ~600 tokens; a smaller window needs less memory.
        self.context = context

    def extract(self, image, lines: list[OcrLine]) -> Extraction:
        response = chat_with_retry(
            self.client,
            model=self.model,
            messages=[
                {"role": "system", "content": instructions(self.doctype)},
                {"role": "user", "content": "OCR text, one printed line per line:\n\n" + lines_to_text(lines)},
            ],
            format=json_schema(self.doctype),
            think=self.think,
            options={"temperature": 0, "num_predict": self.max_tokens, "num_ctx": self.context},
        )

        return Extraction(
            fields=parse_fields(response.message.content or "", self.doctype),
            usage=Usage(
                input_tokens=response.prompt_eval_count or 0,
                output_tokens=response.eval_count or 0,
            ),
        )
