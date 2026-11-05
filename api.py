"""HTTP API: send a document image or PDF, get its fields back.

Run with `uvicorn api:app`. Users pick a mode (profiles.yaml: "accurate",
"private"); experts can name an engine directly (extractor/engines: e1 ... e5).
"""

import io
import time
from functools import lru_cache

import pypdfium2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

import catalogue
from extractor.locate import locate_fields
from extractor.pipeline import Pipeline, build_pipeline, load_engine
from extractor.review import review_notes
from extractor.schema import load_doctype

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PDF_DPI = 200

app = FastAPI(title="Scanned Document Extractor")


@lru_cache(maxsize=None)
def get_pipeline(method: str) -> Pipeline:
    config = load_engine(method)

    return build_pipeline(config, load_doctype(config["doctype"]))


def read_document(data: bytes) -> tuple[Image.Image, int]:
    """The image to extract from and the number of pages in the upload.

    A PDF is rendered page by page at 200 dpi; only the first page is read.
    """

    if data[:5] == b"%PDF-":
        try:
            pdf = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError:
            raise HTTPException(status_code=400, detail="Not a readable PDF")
        pages = len(pdf)
        if pages == 0:
            raise HTTPException(status_code=400, detail="The PDF has no pages")
        image = pdf[0].render(scale=PDF_DPI / 72).to_pil()
        pdf.close()

        return image, pages

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="Not an image or PDF file")

    return image, 1


def choose_method(profile: str | None, method: str | None) -> tuple[str | None, str]:
    """The mode (if one was used) and the engine to run."""

    if method:
        engines = catalogue.engines()
        if method not in engines:
            raise HTTPException(status_code=400, detail=f"Unknown engine {method!r}")
        if not engines[method]["available"]:
            raise HTTPException(status_code=503, detail=engines[method]["unavailable_reason"])
        return None, method

    offered = catalogue.profiles()
    profile = profile or offered["default"]
    if profile not in offered["profiles"]:
        raise HTTPException(status_code=400, detail=f"Unknown mode {profile!r}")
    chosen = offered["profiles"][profile]
    if not chosen["available"]:
        raise HTTPException(status_code=503, detail=chosen["unavailable_reason"])

    return profile, chosen["method"]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/profiles")
def profiles() -> dict:
    return catalogue.profiles()


@app.get("/methods")
def methods() -> dict:
    return {"methods": catalogue.engines()}


@app.post("/extract")
async def extract(
    file: UploadFile = File(...),
    profile: str | None = Form(None),
    method: str | None = Form(None),
) -> dict:
    profile, method = choose_method(profile, method)

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File larger than 10 MB")
    image, pages = read_document(data)

    config = load_engine(method)
    doctype = load_doctype(config["doctype"])

    start = time.perf_counter()
    try:
        result = get_pipeline(method).run(image)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Extraction failed: {error}")
    seconds = time.perf_counter() - start

    usd = None
    prices = config.get("price_per_million_tokens")
    if result.usage is not None and prices:
        usd = (
            result.usage.input_tokens * prices["input"]
            + result.usage.output_tokens * prices["output"]
        ) / 1e6

    fields = doctype.values(result.fields)

    return {
        "profile": profile,
        "method": method,
        "doctype": doctype.name,
        "fields": fields,
        "normalized": doctype.normalize(result.fields),
        "review": review_notes(doctype, result.fields),
        "lines": [{"text": line.text, "box": list(line.box)} for line in result.lines],
        "field_lines": locate_fields(fields, result.lines),
        "seconds": round(seconds, 3),
        "usage": vars(result.usage) if result.usage else None,
        "usd": usd,
        "image_size": list(image.size),
        "pages": pages,
    }
