"""HTTP API: send a document image, get its fields back.

Run with `uvicorn api:app`. Methods are the experiment configs in
evaluation/configs (e2 rules, e3 OpenAI vision, e4 local LLM, e5 local vision).
"""

import io
import os
import time
from functools import lru_cache

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from evaluation.run import CONFIG_DIR, load_config
from extractor.locate import locate_fields
from extractor.pipeline import Pipeline, build_pipeline
from extractor.schema import load_doctype

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
CLOUD_EXTRACTORS = {"openai_vision"}

app = FastAPI(title="Scanned Document Extractor")


def default_method() -> str:
    return os.getenv("DEFAULT_METHOD") or ("e3" if os.getenv("OPENAI_API_KEY") else "e2")


def available_methods() -> dict[str, dict]:
    methods = {}

    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        config = load_config(path.stem)
        methods[path.stem] = {
            "description": config.get("description", ""),
            "doctype": config["doctype"],
            "ocr": config["ocr"],
            "fields": config["fields"],
            "leaves_machine": config["fields"] in CLOUD_EXTRACTORS,
        }

    return methods


@lru_cache(maxsize=None)
def get_pipeline(method: str) -> Pipeline:
    config = load_config(method)

    return build_pipeline(config, load_doctype(config["doctype"]))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/methods")
def methods() -> dict:
    return {"default": default_method(), "methods": available_methods()}


@app.post("/extract")
async def extract(file: UploadFile = File(...), method: str | None = Form(None)) -> dict:
    method = method or default_method()
    if method not in available_methods():
        raise HTTPException(status_code=400, detail=f"Unknown method {method!r}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image larger than 10 MB")

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="Not an image file")

    config = load_config(method)
    doctype = load_doctype(config["doctype"])

    start = time.perf_counter()
    try:
        result = get_pipeline(method).run(image)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"{method} failed: {error}")
    seconds = time.perf_counter() - start

    usd = None
    prices = config.get("price_per_million_tokens")
    if result.usage is not None and prices:
        usd = (
            result.usage.input_tokens * prices["input"]
            + result.usage.output_tokens * prices["output"]
        ) / 1e6

    return {
        "method": method,
        "doctype": doctype.name,
        "fields": doctype.values(result.fields),
        "normalized": doctype.normalize(result.fields),
        "lines": [{"text": line.text, "box": list(line.box)} for line in result.lines],
        "field_lines": locate_fields(doctype.values(result.fields), result.lines),
        "seconds": round(seconds, 3),
        "usage": vars(result.usage) if result.usage else None,
        "usd": usd,
        "image_size": list(image.size),
    }
