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
import local_model
from extractor.gate import decide
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


def choose_method(profile: str | None, method: str | None) -> tuple[str | None, list[str]]:
    """The mode (if one was used) and the engines to run, in turn."""

    if method:
        engines = catalogue.engines()
        if method not in engines:
            raise HTTPException(status_code=400, detail=f"Unknown engine {method!r}")
        if not engines[method]["available"]:
            raise HTTPException(status_code=503, detail=engines[method]["unavailable_reason"])
        return None, [method]

    offered = catalogue.profiles()
    profile = profile or offered["default"]
    if profile not in offered["profiles"]:
        raise HTTPException(status_code=400, detail=f"Unknown mode {profile!r}")
    chosen = offered["profiles"][profile]
    if not chosen["available"]:
        raise HTTPException(status_code=503, detail=chosen["unavailable_reason"])

    return profile, chosen["chain"]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/profiles")
def profiles() -> dict:
    return catalogue.profiles()


@app.get("/methods")
def methods() -> dict:
    return {"methods": catalogue.engines()}


@app.get("/local-model")
def local_model_status() -> dict:
    """The private mode's local model: server running, model downloaded, on the GPU."""

    return local_model.status()


@app.post("/local-model/download")
def local_model_download() -> dict:
    """Download the private mode's model into the Ollama server, in the background."""

    if catalogue.ollama_models() is None:
        raise HTTPException(status_code=503, detail="The local model server (Ollama) is not running")

    return local_model.start_download(local_model.private_model())


@app.post("/extract")
async def extract(
    file: UploadFile = File(...),
    profile: str | None = Form(None),
    method: str | None = Form(None),
) -> dict:
    profile, steps = choose_method(profile, method)

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File larger than 10 MB")
    image, pages = read_document(data)

    # Each engine's gate decides whether its result stands or the document goes
    # to the next engine; the last engine's result stands either way.
    answer, chain, failure = None, [], None
    for number, step in enumerate(steps, start=1):
        try:
            result = run_engine(step, image)
        except Exception as error:
            failure = f"Extraction failed: {error}"
            chain.append({"method": step, "seconds": None, "usd": None, "accepted": None,
                          "reasons": [], "error": str(error)})
            continue
        answer = result
        gate = result["gate"]
        chain.append({"method": step, "seconds": result["seconds"], "usd": result["usd"],
                      "accepted": gate["accepted"] if gate else None,
                      "reasons": gate["reasons"] if gate else [], "error": None})
        # An engine without line items has no gate: its result cannot be
        # checked, so a later engine still gets the document.
        if (gate is not None and gate["accepted"]) or number == len(steps):
            break

    if answer is None:
        raise HTTPException(status_code=502, detail=failure)

    costs = [step["usd"] for step in chain if step["usd"] is not None]

    return {
        "profile": profile,
        **answer,
        # Every engine that ran, in order, with its gate decision; the last
        # one that did not fail gave the result.
        "chain": chain,
        "seconds": round(sum(step["seconds"] or 0 for step in chain), 3),
        "usd": sum(costs) if costs else None,
        "pages": pages,
    }


def run_engine(method: str, image: Image.Image) -> dict:
    """One engine's result on the image, with its checks and gate decision."""

    config = load_engine(method)
    doctype = load_doctype(config["doctype"])

    start = time.perf_counter()
    result = get_pipeline(method).run(image)
    seconds = time.perf_counter() - start

    usd = None
    prices = config.get("price_per_million_tokens")
    if result.usage is not None and prices:
        usd = (
            result.usage.input_tokens * prices["input"]
            + result.usage.output_tokens * prices["output"]
        ) / 1e6

    fields = doctype.values(result.fields)
    review = review_notes(
        doctype,
        result.fields,
        result.line_items.total if result.line_items is not None else None,
    )
    line_items = result.line_items.to_json() if result.line_items is not None else None

    return {
        "method": method,
        "doctype": doctype.name,
        "fields": fields,
        "normalized": doctype.normalize(result.fields),
        "review": review,
        "lines": [{"text": line.text, "box": list(line.box)} for line in result.lines],
        "field_lines": locate_fields(fields, result.lines),
        # Items, subtotal, tax, total and needs_review; None when the engine
        # does not read line items.
        "line_items": line_items,
        # Whether the result can be used without a look (every check passed),
        # or why not; None when the engine reads no line items.
        "gate": decide(line_items, review).to_json() if line_items is not None else None,
        "seconds": round(seconds, 3),
        "usage": vars(result.usage) if result.usage else None,
        "usd": usd,
        "image_size": list(image.size),
    }
