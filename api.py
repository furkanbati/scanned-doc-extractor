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
from pydantic import BaseModel, Field

import catalogue
import local_model
from extractor.efatura import NotAnInvoice, looks_like_xml, read_invoice
from extractor.gate import LINE_ITEMS, decide
from extractor.locate import locate_fields
from extractor.pipeline import Pipeline, build_pipeline, build_template_pipeline, load_engine
from extractor.review import review_notes
from extractor.ocr.base import OcrLine
from extractor.schema import DocumentType, doctype_names, load_doctype, save_doctype
from extractor.templates import read as template_read
from extractor.templates import store as templates
from extractor.templates.learn import MAX_EXAMPLES, MIN_EXAMPLES, Example, learn, suggest
from extractor.templates.values import value_from_lines

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PDF_DPI = 200
# A step that reads with a user's rule set: "template:<file name>".
TEMPLATE = "template:"
# The engine that reads what a rule set cannot: local, like the rule set.
TEMPLATE_FALLBACK = "e5"
# An e-Fatura's XML is read as it is, whatever mode was chosen.
XML_METHOD = "efatura_xml"

app = FastAPI(title="Scanned Document Extractor")


@lru_cache(maxsize=None)
def get_pipeline(method: str) -> Pipeline:
    config = load_engine(method)

    return build_pipeline(config, load_doctype(config["doctype"]))


@lru_cache(maxsize=16)
def get_typed_pipeline(method: str, doctype: str) -> Pipeline:
    """An engine reading another document type than its own (E5 behind a
    rule set for invoices)."""

    return build_pipeline(load_engine(method), load_doctype(doctype))


def pipeline_for(method: str, doctype: str | None) -> Pipeline:
    if doctype is None or doctype == load_engine(method)["doctype"]:
        return get_pipeline(method)

    return get_typed_pipeline(method, doctype)


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

    return learned_answer(name, doctype, examples, replace)


class Line(BaseModel):
    text: str
    box: list[int]


class TaughtExample(BaseModel):
    """An example as the UI holds it: OCR lines from /ocr, the confirmed
    values, and the lines picked per field (indexes into `lines`)."""

    lines: list[Line]
    values: dict[str, str | None] = Field(default_factory=dict)
    picked: dict[str, list[int]] = Field(default_factory=dict)

    def example(self, doctype: DocumentType) -> Example:
        return Example([OcrLine(text=line.text, box=tuple(line.box)) for line in self.lines],
                       doctype.values(self.values), {k: v for k, v in self.picked.items() if v})


class Suggestion(BaseModel):
    doctype: str
    examples: list[TaughtExample]
    lines: list[Line]


class Picked(BaseModel):
    type: str
    lines: list[str]


class Lesson(BaseModel):
    name: str
    doctype: str = "receipt"
    examples: list[TaughtExample]
    replace: bool = False


def doctype_or_404(name: str) -> DocumentType:
    try:
        return load_doctype(name)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))


@app.post("/ocr")
async def ocr(file: UploadFile = File(...)) -> dict:
    """E2's OCR lines and boxes, numbered in reading order: the lines a user
    picks each field's value from when teaching a rule set."""

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File larger than 10 MB")
    image, pages = read_document(data)
    lines = get_pipeline("e2").ocr.read(image)

    return {"lines": [{"text": line.text, "box": list(line.box)} for line in lines],
            "image_size": list(image.size), "pages": pages}


@app.get("/doctypes")
def doctype_list() -> dict:
    """The document types: the project's own and the ones users defined."""

    return {"doctypes": doctype_names()}


@app.post("/doctypes")
def doctype_create(doctype: DocumentType, replace: bool = False) -> dict:
    try:
        save_doctype(doctype, replace=replace)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error))

    return {"saved": doctype.name}


@app.post("/templates/suggest")
def template_suggest(request: Suggestion) -> dict:
    """Per field, the lines and value the examples so far point to on a new
    example; the user then only checks them."""

    doctype = doctype_or_404(request.doctype)
    examples = [example.example(doctype) for example in request.examples]
    lines = [OcrLine(text=line.text, box=tuple(line.box)) for line in request.lines]

    return {"fields": suggest(doctype, examples, lines)}


@app.post("/templates/value")
def template_value(picked: Picked) -> dict:
    """The value in the lines a user picked, as learning will read it: the
    date or amount in them, or the text after its label."""

    return {"value": value_from_lines(picked.type, picked.lines)}


@app.post("/templates/learn")
def template_learn(lesson: Lesson) -> dict:
    """Learn and save a rule set from examples with their OCR lines and the
    lines picked per field (as /ocr and /templates/suggest gave them)."""

    try:
        templates.slug(lesson.name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if not MIN_EXAMPLES <= len(lesson.examples) <= MAX_EXAMPLES:
        raise HTTPException(status_code=400, detail=f"Send {MIN_EXAMPLES} to {MAX_EXAMPLES} examples of the same format")
    if templates.path(lesson.name).exists() and not lesson.replace:
        raise HTTPException(status_code=409, detail=f"A rule set named {lesson.name!r} already exists")
    doctype = doctype_or_404(lesson.doctype)

    return learned_answer(lesson.name, doctype, [example.example(doctype) for example in lesson.examples],
                          lesson.replace)


def learned_answer(name: str, doctype: DocumentType, examples: list[Example], replace: bool) -> dict:
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

    return {"status": "saved", "file": slug, "template": templates.names()[slug],
            # Optional fields too few examples printed: read by no rule.
            "skipped": learned.skipped}


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
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File larger than 10 MB")
    # Needs no engine, so it is read even when the chosen one cannot run here.
    if looks_like_xml(data):
        answer = read_xml(data)
        return {"profile": profile, **answer, "warning": None, "pages": 1,
                "chain": [{"method": XML_METHOD, "seconds": answer["seconds"], "usd": None,
                           "accepted": answer["gate"]["accepted"], "reasons": answer["gate"]["reasons"],
                           "error": None}]}
    profile, steps = choose_method(profile, method, template)
    image, pages = read_document(data)

    # Each engine's gate decides whether its result stands or the document goes
    # to the next engine; the last engine's result stands either way.
    # With a rule set, every engine reads its document type (E5 an invoice).
    doctype = templates.load(template)["doctype"] if template else None
    answer, chain, failure = None, [], None
    for number, step in enumerate(steps, start=1):
        try:
            result = run_engine(step, image, doctype)
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


def read_xml(data: bytes) -> dict:
    """An e-Fatura (UBL-TR XML): its tags give the invoice's fields and items.
    Accepted unless a field is missing or its own amounts do not add up."""

    start = time.perf_counter()
    try:
        invoice = read_invoice(data)
    except NotAnInvoice as error:
        raise HTTPException(status_code=400, detail=str(error))
    doctype = load_doctype("invoice")
    review = review_notes(doctype, invoice.fields)
    line_items = invoice.line_items.to_json()
    reasons = [f"{name} {note}" for name, note in review.items()]
    reasons += [LINE_ITEMS + reason for reason in invoice.line_items.reasons]

    return {
        "method": XML_METHOD,
        "template": None,
        "doctype": doctype.name,
        "fields": doctype.values(invoice.fields),
        "normalized": doctype.normalize(invoice.fields),
        "review": review,
        "lines": [],
        "field_lines": {},
        "line_items": line_items,
        "gate": {"accepted": not reasons, "reasons": reasons},
        "seconds": round(time.perf_counter() - start, 3),
        "usage": None,
        "usd": None,
        "image_size": None,
        # ETTN, currency, scenario, invoice type, buyer.
        "meta": invoice.meta,
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


def run_engine(method: str, image: Image.Image, doctype_name: str | None = None) -> dict:
    """One engine's result on the image, with its checks and gate decision.

    `doctype_name`: read this document type instead of the engine's own.
    """

    template = None
    if method.startswith(TEMPLATE):
        name = method[len(TEMPLATE):]
        template, pipeline = get_template_pipeline(name, templates.path(name).stat().st_mtime)
        config = load_engine("e2")
        doctype = load_doctype(template["doctype"])
    else:
        config = load_engine(method)
        pipeline = pipeline_for(method, doctype_name)
        doctype = load_doctype(doctype_name or config["doctype"])

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
        "meta": None,
    }
