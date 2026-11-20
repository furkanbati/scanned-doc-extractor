"""HTTP API: send a document image or PDF, get its fields back.

Run with `uvicorn api:app`. Users pick a mode (profiles.yaml: "accurate",
"private"); experts can name an engine directly (extractor/engines: e1 ... e5).
A user can also teach a rule set from three documents of one format
(/templates) and read that format with it, locally and without a model; the
documents it cannot read go to E5.
"""

import io
import json
import time
from functools import lru_cache

import pypdfium2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

import catalogue
import local_model
from extractor.gate import decide
from extractor.locate import locate_fields
from extractor.pipeline import Pipeline, build_pipeline, build_template_pipeline, load_engine
from extractor.review import review_notes
from extractor.schema import load_doctype
from extractor.templates import read as template_read
from extractor.templates import store as templates
from extractor.templates.learn import MAX_EXAMPLES, MIN_EXAMPLES, Example, learn

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PDF_DPI = 200
# A step that reads with a user's rule set: "template:<file name>".
TEMPLATE = "template:"
# The engine that reads what a rule set cannot: local, like the rule set.
TEMPLATE_FALLBACK = "e5"

app = FastAPI(title="Scanned Document Extractor")


@lru_cache(maxsize=None)
def get_pipeline(method: str) -> Pipeline:
    config = load_engine(method)

    return build_pipeline(config, load_doctype(config["doctype"]))


@lru_cache(maxsize=16)
def get_template_pipeline(name: str, modified: float) -> tuple[dict, Pipeline]:
    """A saved rule set and its pipeline; read again when the file changes."""

    template = templates.load(name)

    return template, build_template_pipeline(template, load_doctype(template["doctype"]))


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


def choose_method(profile: str | None, method: str | None, template: str | None = None) -> tuple[str | None, list[str]]:
    """The mode (if one was used) and the engines to run, in turn."""

    if template:
        try:
            templates.load(template)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error.args[0]))
        steps = [TEMPLATE + templates.slug(template)]
        if catalogue.engines()[TEMPLATE_FALLBACK]["available"]:
            steps.append(TEMPLATE_FALLBACK)
        return None, steps

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


@app.get("/templates")
def template_list() -> dict:
    """The user's saved rule sets: file name -> name, date, examples, rule kinds."""

    return {"templates": templates.names(), "fallback": TEMPLATE_FALLBACK,
            "fallback_available": catalogue.engines()[TEMPLATE_FALLBACK]["available"],
            "min_examples": MIN_EXAMPLES, "max_examples": MAX_EXAMPLES,
            # The fields a rule set learns: those of E2's document type.
            "fields": load_doctype(load_engine("e2")["doctype"]).field_names}


@app.post("/templates")
async def template_create(
    name: str = Form(...),
    files: list[UploadFile] = File(...),
    values: str = Form(..., description="JSON list: the confirmed fields of each file, in order"),
    replace: bool = Form(False),
) -> dict:
    """Learn a rule set from documents of one format and the values the user
    confirmed on them, and save it under the user's name.

    When the examples do not agree on a field, nothing is saved: the answer
    names the fields and, where one example does not fit the others, which
    one to replace ("status": "needs_examples").
    """

    try:
        templates.slug(name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if not MIN_EXAMPLES <= len(files) <= MAX_EXAMPLES:
        raise HTTPException(status_code=400, detail=f"Send {MIN_EXAMPLES} to {MAX_EXAMPLES} documents of the same format")
    try:
        confirmed = json.loads(values)
    except ValueError:
        raise HTTPException(status_code=400, detail="values must be a JSON list")
    if not isinstance(confirmed, list) or len(confirmed) != len(files) or not all(isinstance(v, dict) for v in confirmed):
        raise HTTPException(status_code=400, detail="values needs one object of fields per file")
    if templates.path(name).exists() and not replace:
        raise HTTPException(status_code=409, detail=f"A rule set named {name!r} already exists")

    doctype = load_doctype(load_engine("e2")["doctype"])
    ocr = get_pipeline("e2").ocr
    examples = []
    for upload, fields in zip(files, confirmed):
        data = await upload.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{upload.filename} is larger than 10 MB")
        image, _ = read_document(data)
        examples.append(Example(ocr.read(image), doctype.values(fields)))

    learned = learn(name, doctype, examples)
    if learned.template is None:
        return {
            "status": "needs_examples",
            "unstable": learned.unstable,
            # Field -> the example (from 1) to replace with another document.
            "misfits": {field: index + 1 for field, index in learned.misfits.items()},
        }

    templates.save(learned.template, replace=replace)
    get_template_pipeline.cache_clear()
    slug = templates.slug(name)

    return {"status": "saved", "file": slug, "template": templates.names()[slug]}


@app.delete("/templates/{name}")
def template_delete(name: str) -> dict:
    try:
        templates.delete(name)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error.args[0]))
    get_template_pipeline.cache_clear()

    return {"deleted": name}


@app.post("/extract")
async def extract(
    file: UploadFile = File(...),
    profile: str | None = Form(None),
    method: str | None = Form(None),
    template: str | None = Form(None, description="a saved rule set; E5 reads what it cannot"),
) -> dict:
    profile, steps = choose_method(profile, method, template)

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
        # Set when the rule set did not read the document: who did, and why.
        "warning": template_warning(chain, answer["method"]) if template else None,
        # Every engine that ran, in order, with its gate decision; the last
        # one that did not fail gave the result.
        "chain": chain,
        "seconds": round(sum(step["seconds"] or 0 for step in chain), 3),
        "usd": sum(costs) if costs else None,
        "pages": pages,
    }


def template_warning(chain: list[dict], answered_by: str) -> str | None:
    """What the user must know when the rule set did not read the document."""

    first = chain[0]
    if first["accepted"]:
        return None
    why = "; ".join(first["reasons"]) or first["error"] or "it failed"
    if answered_by == TEMPLATE_FALLBACK:
        return f"The rule set did not fit this document ({why}). E5 read it instead: check the result."
    if len(chain) > 1:
        return f"The rule set did not fit this document ({why}), and E5 failed too: check the result."

    return f"The rule set did not fit this document ({why}). E5 is not available here: check the result."


def run_engine(method: str, image: Image.Image) -> dict:
    """One engine's result on the image, with its checks and gate decision."""

    template = None
    if method.startswith(TEMPLATE):
        name = method[len(TEMPLATE):]
        template, pipeline = get_template_pipeline(name, templates.path(name).stat().st_mtime)
        config = load_engine("e2")
    else:
        config = load_engine(method)
        pipeline = get_pipeline(method)
    doctype = load_doctype(config["doctype"])

    start = time.perf_counter()
    result = pipeline.run(image)
    seconds = time.perf_counter() - start

    usd = None
    prices = config.get("price_per_million_tokens")
    if result.usage is not None and prices:
        usd = (
            result.usage.input_tokens * prices["input"]
            + result.usage.output_tokens * prices["output"]
        ) / 1e6

    fields = doctype.values(result.fields)
    # A rule set's gate does not compare the total with E2's line items: on
    # the measured formats that only sent right results away (reports/templates.md).
    review = review_notes(
        doctype,
        result.fields,
        result.line_items.total if result.line_items is not None and template is None else None,
    )
    line_items = result.line_items.to_json() if result.line_items is not None else None
    if template is not None:
        gate = template_read.decide(template, doctype, result.lines, review).to_json()
    elif line_items is not None:
        gate = decide(line_items, review).to_json()
    else:
        gate = None

    return {
        "method": method,
        "template": template["name"] if template else None,
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
        "gate": gate,
        "seconds": round(seconds, 3),
        "usage": vars(result.usage) if result.usage else None,
        "usd": usd,
        "image_size": list(image.size),
    }
