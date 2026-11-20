"""Streamlit interface: upload scanned documents, check and correct the fields, download them.

Run with `streamlit run ui/app.py`; set API_URL to point at the API.
"""

import hashlib
import io
import os

import pandas
import pypdfium2
import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

from export import columns, line_item_rows, result_rows, to_csv, to_json, to_xlsx

API_URL = os.getenv("API_URL", "http://localhost:8000")
PDF_DPI = 200  # the API renders PDFs at the same scale, so boxes line up

# One colour per field position; fields beyond the palette reuse it.
COLOURS = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed", "#0891b2"]
BOOKKEEPING = ["file", "check", "engine", "seconds", "usd"]

st.set_page_config(page_title="Scanned Document Extractor", page_icon="🧾", layout="wide")


@st.cache_data(ttl=30)
def load_catalogue() -> tuple[dict, dict]:
    profiles = requests.get(f"{API_URL}/profiles", timeout=10)
    profiles.raise_for_status()
    engines = requests.get(f"{API_URL}/methods", timeout=10)
    engines.raise_for_status()

    return profiles.json(), engines.json()["methods"]


def load_local() -> dict | None:
    """The private mode's local model: server, download, GPU (api.py /local-model)."""

    try:
        response = requests.get(f"{API_URL}/local-model", timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        return None

    return response.json()


CPU_SPEED = ("On a laptop CPU it takes about 25 to 35 s per receipt "
             "(measured: 26 s in Docker with 8 threads).")
GPU_SPEED = ("Measured on a laptop's Intel Arc 130T integrated GPU: 12 s per receipt against 33 s on "
             "its CPU, about 2.8 times faster (21 s when it also reads the line items). A dedicated "
             "NVIDIA card should be faster still; that was not measured.")
GPU_STEPS = """**The GPU is used only when the Ollama server runs where it can reach the GPU.**
The model is the same either way; only where Ollama runs changes.

- **NVIDIA GPU:** needs a recent NVIDIA driver with CUDA; `nvidia-smi` should list the card.
  Restart with `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`
  (on Linux also install the NVIDIA Container Toolkit).
- **Intel or AMD GPU:** Docker cannot use these. Install Ollama on this computer
  (ollama.com); for an Intel integrated GPU set the environment variable
  `OLLAMA_IGPU_ENABLE=1` and restart Ollama. Then add
  `OLLAMA_HOST=http://host.docker.internal:11434` to `.env` and run `docker compose up -d`.
- **No such GPU, or no CUDA driver:** choose the CPU; nothing else to install.
"""


def model_download(local: dict) -> None:
    """Start the download, show its progress, or say what is missing."""

    if not local["running"]:
        st.warning(f"No Ollama server answers at {local['host']}. The bundled one starts with "
                   "`docker compose up -d ollama`; Ollama on this computer must be running.")
        if st.button("Check again"):
            st.rerun()
        return

    state = local["download"]
    if local["downloaded"]:
        st.success(f"{local['model']} is ready at {local['host']}. The private mode runs E5.")
        return

    if state["status"] == "running":
        download_progress()
        return
    if state["status"] == "failed":
        st.error(f"The download failed: {state['error']}")

    st.caption(f"The model goes to the Ollama server at {local['host']}.")
    if st.button(f"Download {local['model']} (about 2 GB, once)", type="primary"):
        try:
            requests.post(f"{API_URL}/local-model/download", timeout=30).raise_for_status()
        except requests.RequestException as error:
            st.error(f"Could not start the download: {error}")
            return
        st.rerun()


@st.fragment(run_every=2)
def download_progress() -> None:
    """Redrawn every 2 s on its own, so the rest of the page stays usable."""

    local = load_local()
    state = local["download"] if local else {"status": "failed"}
    if state["status"] != "running":
        st.rerun(scope="app")

    share = state["completed"] / state["total"] if state["total"] else 0.0
    st.progress(share, text=f"Downloading {local['model']}: "
                            f"{state['completed'] / 1e9:.2f} of {state['total'] / 1e9:.2f} GB so far")
    st.caption("Ollama reports the size of each part as it starts, so the total grows at first. "
               "You can use the page meanwhile.")


def setup_panel(local: dict) -> None:
    """Asks once per session how the private mode should run, and sets it up."""

    with st.container(border=True):
        st.subheader("Set up the private mode")
        st.write("The private mode can read documents with a local vision model (E5: 82% of fields "
                 "right on the test receipts) without sending them anywhere. Until it is set up it "
                 "runs OCR and rules (E2: 63%).")
        way = st.radio(
            "How should the local model run?",
            ["gpu", "cpu", "skip"],
            format_func={"gpu": "On this computer's GPU (faster, needs Ollama where it can reach the GPU)",
                         "cpu": "On the CPU, in Docker (nothing to install)",
                         "skip": "Not now: keep OCR and rules"}.get,
            key="setup_way",
        )
        if way == "skip":
            st.caption("You can set it up later from the sidebar.")
            if st.button("Close"):
                st.session_state.setup_closed = True
                st.rerun()
            return

        if way == "gpu":
            st.markdown(GPU_STEPS)
            st.caption(GPU_SPEED)
        else:
            st.caption(CPU_SPEED)
        model_download(local)


def extract(data: bytes, filename: str, choice: tuple[str, str]) -> dict:
    kind, name = choice
    response = requests.post(
        f"{API_URL}/extract",
        files={"file": (filename, data)},
        data={kind: name},
        timeout=900,
    )
    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(detail)

    return response.json()


def open_document(data: bytes) -> Image.Image:
    if data[:5] == b"%PDF-":
        pdf = pypdfium2.PdfDocument(data)
        image = pdf[0].render(scale=PDF_DPI / 72).to_pil()
        pdf.close()
        return image

    return Image.open(io.BytesIO(data))


def draw_fields(image: Image.Image, result: dict) -> Image.Image:
    """The image with each field's OCR lines outlined in the field's colour."""

    canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    width = max(2, canvas.width // 300)

    for position, indexes in enumerate(result["field_lines"].values()):
        colour = COLOURS[position % len(COLOURS)]
        for index in indexes:
            box = result["lines"][index]["box"]
            draw.polygon(list(zip(box[0::2], box[1::2])), outline=colour, width=width)

    return canvas


def load_templates() -> dict:
    """The saved rule sets (api.py /templates), or none if the API is older."""

    try:
        response = requests.get(f"{API_URL}/templates", timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        return {"templates": {}, "fallback_available": False, "min_examples": 3, "max_examples": 6,
                "fields": ["company", "address", "date", "total"]}

    return response.json()


SAME_FORMAT = ("A rule set reads **one format**: documents laid out alike, such as one supplier's invoices. "
               "It is learned from your examples on this server, without a model, and reads each document "
               "in a few seconds at no cost. Documents it cannot read go to E5, the local model, and are "
               "marked for a check. If your documents come in many different layouts, choose E3 (cloud) or, "
               "to keep them on this computer, E5 (the private mode) instead.")
FIELD_TYPES = ["text", "date", "amount"]


def api_post(path: str, timeout: int = 60, **kwargs) -> dict:
    response = requests.post(f"{API_URL}{path}", timeout=timeout, **kwargs)
    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(detail)

    return response.json()


def load_doctypes() -> dict:
    try:
        response = requests.get(f"{API_URL}/doctypes", timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        return {}

    return response.json()["doctypes"]


def draw_lines(image: Image.Image, lines: list[dict], picked: dict[str, list[int]]) -> Image.Image:
    """Every OCR line boxed and numbered; the picked ones in their field's colour."""

    canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    width = max(1, canvas.width // 500)
    size = max(12, canvas.width // 60)
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:  # Pillow before 10.1: one small bitmap font
        font = ImageFont.load_default()
    colour_of = {index: COLOURS[position % len(COLOURS)]
                 for position, indexes in enumerate(picked.values()) for index in indexes}
    for index, line in enumerate(lines):
        box = line["box"]
        colour = colour_of.get(index, "#9ca3af")
        draw.polygon(list(zip(box[0::2], box[1::2])), outline=colour, width=width * (3 if index in colour_of else 1))
        draw.text((max(0, min(box[0::2]) - size * 2), min(box[1::2])), str(index), fill="#dc2626", font=font)

    return canvas


def new_doctype_form() -> None:
    """Define a document type of your own: its fields, their kinds, which may be missing."""

    with st.expander("Define a new document type"):
        name = st.text_input("Type name (lower case, e.g. purchase_order)", key="new_type_name")
        description = st.text_input("What the document is", key="new_type_description",
                                    help="E5 reads this when it reads a document the rule set cannot.")
        fields = st.data_editor(
            pandas.DataFrame([{"name": "", "type": "text", "optional": False, "description": ""}]),
            num_rows="dynamic", hide_index=True, width="stretch", key="new_type_fields",
            column_config={"type": st.column_config.SelectboxColumn("type", options=FIELD_TYPES, required=True),
                           "optional": st.column_config.CheckboxColumn("optional")},
        )
        if st.button("Save the document type", disabled=not name):
            rows = [row for row in fields.to_dict("records") if (row.get("name") or "").strip()]
            spec = {"name": name.strip(), "description": description,
                    "fields": [{"name": row["name"].strip(), "type": row["type"] or "text",
                                "optional": bool(row.get("optional")), "description": row.get("description") or ""}
                               for row in rows]}
            try:
                api_post("/doctypes", json=spec)
            except (requests.RequestException, RuntimeError) as error:
                st.error(str(error))
                return
            st.session_state.chosen_type = spec["name"]
            st.rerun()


def rule_sets_page() -> None:
    """Teach a rule set by picking, on each example, the OCR lines of each field."""

    st.subheader("Rule sets for documents of one format")
    st.markdown(SAME_FORMAT)
    saved = load_templates()
    doctypes = load_doctypes()
    if "rule_set_saved" in st.session_state:
        st.success(f"Saved the rule set {st.session_state.pop('rule_set_saved')!r}. Choose it under "
                   "Extract → Advanced → Read with one of my rule sets.")
    if not saved["fallback_available"]:
        st.warning("E5 is not set up here, so documents a rule set cannot read are only marked for a check.")

    with st.container(border=True):
        st.markdown("**New rule set**")
        names = list(doctypes) or ["receipt"]
        wanted = st.session_state.get("chosen_type")
        doctype = st.selectbox("Document type", names, index=names.index(wanted) if wanted in names else 0,
                               format_func=lambda key: f"{key} ({', '.join(f['name'] for f in doctypes[key]['fields'])})"
                               if key in doctypes else key)
        new_doctype_form()
        name = st.text_input("Rule set name", placeholder="e.g. ABC Supplies invoices")
        low, high = saved["min_examples"], saved["max_examples"]
        uploads = st.file_uploader(f"{low} to {high} documents of the same format", type=["jpg", "jpeg", "png", "pdf"],
                                   accept_multiple_files=True, key="teach_files")
        if not uploads or not low <= len(uploads) <= high:
            st.caption(f"Add {low} to {high} documents of this format"
                       + (f"; {len(uploads)} added." if uploads else ". Varied ones help: one with a rounding "
                          "line, a longer address."))
        else:
            teach_examples(name.strip(), doctype, doctypes.get(doctype, {"fields": []})["fields"],
                           [(upload.name, upload.getvalue()) for upload in uploads])

    st.markdown("**Saved rule sets**")
    if not saved["templates"]:
        st.caption("None yet.")
    for file, info in saved["templates"].items():
        columns_ = st.columns([4, 1])
        rules = ", ".join(f"{field}: {kind}" for field, kind in info["fields"].items())
        columns_[0].markdown(f"**{info['name']}** · {info['doctype']} · {info['examples']} examples · "
                             f"{info['created']}  \n<span style='opacity:0.7'>{rules}</span>", unsafe_allow_html=True)
        if columns_[1].button("Delete", key="delete-" + file):
            requests.delete(f"{API_URL}/templates/{file}", timeout=10)
            st.rerun()


def teach_examples(name: str, doctype: str, fields: list[dict], documents: list[tuple[str, bytes]]) -> None:
    """One example at a time: pick each field's lines, check the value, go on."""

    key = hashlib.sha1(repr([doctype, *(file_key(n, d) for n, d in documents)]).encode()).hexdigest()[:12]
    state = st.session_state.setdefault("teach", {})
    if state.get("key") != key:
        state.clear()
        state.update(key=key, current=0, ocr=None, examples=[{"values": {}, "picked": {}} for _ in documents])
    if state["ocr"] is None:
        if st.button("Read the examples", type="primary"):
            with st.spinner("Reading the text on each example..."):
                try:
                    state["ocr"] = [api_post("/ocr", files={"file": (n, d)})["lines"] for n, d in documents]
                except (requests.RequestException, RuntimeError) as error:
                    st.error(f"Could not read the examples: {error}")
                    return
                if doctype == "receipt":
                    prefill_receipt(state, documents)
            st.rerun()
        st.caption("The OCR finds every line of text on each example and numbers it; you pick the lines "
                   "each field is printed on.")
        return

    current = state["current"]
    file, data = documents[current]
    lines = state["ocr"][current]
    example = state["examples"][current]
    st.markdown(f"**Example {current + 1} of {len(documents)}: {file}**")
    if current == 0 and not example["picked"]:
        st.caption("Pick the line(s) of each field. The next examples come with them picked; you only check.")

    left, right = st.columns([1, 1])
    options = list(range(len(lines)))
    label = lambda index: f"{index} · {lines[index]['text']}"
    for field in fields:
        with right:
            picked = example["picked"].get(field["name"], [])
            title = field["name"] + (" (optional)" if field.get("optional") else "")
            if field["type"] == "text":
                chosen = st.multiselect(title, options, default=[i for i in picked if i in options],
                                        format_func=label, key=f"pick-{key}-{current}-{field['name']}")
            else:
                choice = st.selectbox(title, [None, *options], index=(picked[0] + 1) if picked else 0,
                                      format_func=lambda i: "(not on this document)" if i is None else label(i),
                                      key=f"pick-{key}-{current}-{field['name']}")
                chosen = [] if choice is None else [choice]
            if chosen != picked:
                example["picked"][field["name"]] = chosen
                example["values"][field["name"]] = picked_value(field["type"], [lines[i]["text"] for i in chosen])
            if chosen:
                value = st.text_input(f"{field['name']} value", example["values"].get(field["name"]) or "",
                                      key=f"value-{key}-{current}-{field['name']}-{chosen}",
                                      label_visibility="collapsed")
                example["values"][field["name"]] = value or None
    with left:
        st.image(draw_lines(open_document(data), lines, example["picked"]),
                 caption="Every OCR line, numbered; the picked ones in their field's colour")

    back, forward = st.columns(2)
    if current > 0 and back.button("← Previous example"):
        state["current"] -= 1
        st.rerun()
    if current + 1 < len(documents):
        if forward.button("Next example →", type="primary"):
            state["current"] += 1
            suggest_next(state, doctype)
            st.rerun()
    elif forward.button("Learn the rule set", type="primary", disabled=not name):
        learn_rule_set(name, doctype, state, documents)
    if not name:
        st.caption("Give the rule set a name to learn it.")


def picked_value(kind: str, texts: list[str]) -> str | None:
    if not texts:
        return None
    try:
        return api_post("/templates/value", json={"type": kind, "lines": texts}, timeout=10)["value"]
    except (requests.RequestException, RuntimeError):
        return " ".join(texts)


def prefill_receipt(state: dict, documents: list[tuple[str, bytes]]) -> None:
    """On receipts E2 knows the fields: the first example comes with its lines picked."""

    file, data = documents[0]
    try:
        result = extract(data, file, ("method", "e2"))
    except (requests.RequestException, RuntimeError):
        return
    example = state["examples"][0]
    for field, indexes in result["field_lines"].items():
        if indexes and result["fields"].get(field):
            example["picked"][field] = indexes
            example["values"][field] = result["fields"][field]


def suggest_next(state: dict, doctype: str) -> None:
    """Pick the lines of the example now shown from the ones before it, where
    the user has not picked them yet."""

    current = state["current"]
    example = state["examples"][current]
    if example["picked"]:
        return
    earlier = [{"lines": state["ocr"][i], **state["examples"][i]} for i in range(current)]
    try:
        found = api_post("/templates/suggest", json={"doctype": doctype, "examples": earlier,
                                                      "lines": state["ocr"][current]})["fields"]
    except (requests.RequestException, RuntimeError):
        return
    for field, suggestion in found.items():
        example["picked"][field] = suggestion["lines"]
        example["values"][field] = suggestion["value"]


def learn_rule_set(name: str, doctype: str, state: dict, documents: list, replace: bool = False) -> None:
    examples = [{"lines": lines, **example} for lines, example in zip(state["ocr"], state["examples"])]
    try:
        body = api_post("/templates/learn", timeout=120, json={"name": name, "doctype": doctype,
                                                                "examples": examples, "replace": replace})
    except (requests.RequestException, RuntimeError) as error:
        st.error(str(error))
        return

    if body["status"] == "saved":
        # Shown after the rerun, which also lists the new rule set.
        st.session_state.rule_set_saved = name
        st.session_state.teach = {}
        st.rerun()

    st.warning("The examples do not agree yet, so nothing was saved.")
    for field, why in body["unstable"].items():
        misfit = body["misfits"].get(field)
        if misfit:
            st.markdown(f"- **{field}**: example {misfit} ({documents[misfit - 1][0]}) is laid out differently "
                        "from the others. Replace it with another document, or check its lines.")
        else:
            st.markdown(f"- **{field}**: {why}. Check the lines picked for it, or add another example.")


def describe(measured: dict | None) -> str:
    if not measured:
        return "Not measured yet."

    cost = "free" if not measured["usd_per_1000"] else f"${measured['usd_per_1000']:.2f} per 1,000"

    return (
        f"{measured['field_accuracy']:.0%} of fields correct on {measured['receipts']} test receipts"
        f" · about {measured['seconds_per_document']:.0f} s each · {cost}"
    )


def engine_label(name: str, engine: dict) -> str:
    model = f" ({engine['model']})" if engine["model"] else ""
    where = "cloud" if engine["leaves_machine"] else "on this server"
    state = "" if engine["available"] else " — not available"

    return f"{name}: {engine['ocr']} → {engine['fields']}{model}, {where}{state}"


def file_key(name: str, data: bytes) -> str:
    return f"{name}:{hashlib.sha1(data).hexdigest()[:12]}"


# ---------------------------------------------------------------- settings

st.title("Scanned Document Extractor")
st.caption("Upload scanned receipts, check the fields, download them as a spreadsheet.")

try:
    catalogue, engines = load_catalogue()
except requests.RequestException as error:
    st.error(f"The extraction service is not reachable at {API_URL}: {error}")
    st.stop()

profiles = catalogue["profiles"]
names = list(profiles)
local = load_local()
private = profiles.get("private")
# The private mode still runs its fallback because the local model is not set up.
needs_setup = bool(local and private and private.get("fallback_reason"))
if needs_setup and local["downloaded"] and not st.session_state.get("refreshed_after_download"):
    # Just downloaded: the cached modes still name the fallback.
    st.session_state.refreshed_after_download = True
    st.session_state.just_ready = True
    load_catalogue.clear()
    st.rerun()
if st.session_state.pop("just_ready", False) and not needs_setup:
    st.toast("The local model is ready: the private mode now runs E5.")

with st.sidebar:
    page = st.radio("Page", ["Extract", "Rule sets"], horizontal=True, label_visibility="collapsed")

if page == "Rule sets":
    rule_sets_page()
    st.stop()

with st.sidebar:
    st.header("Mode")
    profile = st.radio(
        "Mode",
        names,
        index=names.index(catalogue["default"]),
        format_func=lambda name: profiles[name]["label"],
        label_visibility="collapsed",
    )
    chosen = profiles[profile]
    st.write(chosen["description"])
    if chosen.get("fallback_reason"):
        st.info(f"Running {chosen['method'].upper()} for now. {chosen['fallback_reason']}.")
    if profile == "private" and needs_setup and st.session_state.get("setup_closed") and st.button("Set up the local model"):
        st.session_state.setup_closed = False
        st.rerun()
    if profile == "private" and local and local["downloaded"] and not chosen.get("fallback_reason"):
        where = {True: "on the GPU", False: "on the CPU"}.get(local["on_gpu"], "GPU or CPU shows after the first document")
        st.caption(f"Local model {local['model']} · {where}")
    st.caption(describe(chosen["measured"]))
    choice = ("profile", profile)
    available, reason, leaves = chosen["available"], chosen["unavailable_reason"], chosen["leaves_machine"]
    measured = chosen["measured"]

    with st.expander("Advanced"):
        if st.checkbox("Choose the engine myself"):
            engine_names = list(engines)
            default_engine = engine_names.index(chosen["method"])
            method = st.selectbox(
                "Engine",
                engine_names,
                index=default_engine,
                format_func=lambda name: engine_label(name, engines[name]),
            )
            engine = engines[method]
            st.caption(engine["description"])
            st.caption(describe(engine["measured"]))
            choice = ("method", method)
            available, reason, leaves = engine["available"], engine["unavailable_reason"], engine["leaves_machine"]
            measured = engine["measured"]

        saved = load_templates()["templates"]
        if saved and st.checkbox("Read with one of my rule sets"):
            file = st.selectbox("Rule set", list(saved), format_func=lambda key: saved[key]["name"])
            st.caption("Only for documents of the format it was taught; the others go to E5 and are marked "
                       "for a check. Free, on this server.")
            choice = ("template", file)
            available, reason, leaves, measured = True, None, False, None
        elif not saved:
            st.caption("Rule sets for documents of one format: see the Rule sets page.")

        st.markdown("**Planned providers**")
        for provider in catalogue["planned"]:
            st.markdown(f"<span style='opacity:0.45'>○ {provider} — planned</span>", unsafe_allow_html=True)

    if leaves:
        st.warning("Documents are sent to a cloud API (OpenAI) in this mode.")
    if not available:
        st.error(f"Not available on this server: {reason}")

# ---------------------------------------------------------------- local model setup

if needs_setup and not st.session_state.get("setup_closed"):
    setup_panel(local)

# ---------------------------------------------------------------- upload and run

if "results" not in st.session_state:
    st.session_state.results = {}
results = st.session_state.results

uploads = st.file_uploader(
    "Receipt images or PDFs (first page is read)",
    type=["jpg", "jpeg", "png", "pdf"],
    accept_multiple_files=True,
)
if not uploads:
    st.info("Add one or more documents to start.")
    st.stop()

documents = [(upload.name, upload.getvalue()) for upload in uploads]
keys = [(file_key(name, data), choice) for name, data in documents]
pending = [index for index, key in enumerate(keys) if key not in results]

if pending:
    estimate = ""
    if measured and measured["usd_per_1000"]:
        estimate = f" · estimated cost ${measured['usd_per_1000'] * len(pending) / 1000:.4f}"
    if st.button(f"Extract {len(pending)} document(s){estimate}", type="primary", disabled=not available):
        progress = st.progress(0.0)
        for step, index in enumerate(pending, start=1):
            name, data = documents[index]
            progress.progress((step - 1) / len(pending), text=f"Reading {name} ({step} of {len(pending)})")
            try:
                item = {"file": name, "result": extract(data, name, choice), "error": None}
            except (requests.RequestException, RuntimeError) as error:
                item = {"file": name, "result": None, "error": str(error)}
            results[keys[index]] = item
        progress.empty()
        st.rerun()

done = [(index, results[key]) for index, key in enumerate(keys) if key in results]
if not done:
    st.stop()

# ---------------------------------------------------------------- table

items = [item for _, item in done]
rows = result_rows(items)
ok = [item for item in items if item["result"]]

gated = [item["result"]["gate"] for item in ok if item["result"].get("gate") is not None]
summary = st.columns(5 if gated else 4)
summary[0].metric("Documents", len(items))
summary[1].metric("To check", sum(1 for row in rows if row["check"]))
summary[2].metric("Time", f"{sum(item['result']['seconds'] for item in ok):.1f} s")
summary[3].metric("Cost", f"${sum(item['result']['usd'] or 0 for item in ok):.4f}")
if gated:
    # Passed every check: fields, sums, no add-on lines (extractor/gate.py).
    summary[4].metric("Accepted without a look", sum(1 for gate in gated if gate["accepted"]),
                      help="Every check passed. On CORD about 1 in 40 of these was still wrong.")

st.subheader("Fields")
st.caption("Edit any cell to correct it; downloads include your corrections. "
           "The check column lists what looks wrong; a misread name can still look right.")

# A new set of documents starts fresh tables, so edits never land on the wrong row.
editor_key = hashlib.sha1(repr([key for key in keys if key in results]).encode()).hexdigest()[:12]

table = pandas.DataFrame(rows, columns=columns(rows))
edited = st.data_editor(
    table,
    disabled=[name for name in BOOKKEEPING if name in table.columns],
    hide_index=True,
    width="stretch",
    column_config={
        "seconds": st.column_config.NumberColumn("seconds", format="%.1f"),
        "usd": st.column_config.NumberColumn("USD", format="$%.5f"),
    },
    key="editor-" + editor_key,
)
final = edited.astype(object).where(edited.notna(), None).to_dict("records")

item_rows = line_item_rows(items)
final_items = []
if item_rows:
    st.subheader("Line items (experimental)")
    st.caption("Each purchased item with its amount, read from the receipt by the chosen engine. "
               "Where the items, subtotal, tax and total do not add up, check says why; "
               "edit any cell to correct it.")
    items_table = pandas.DataFrame(item_rows, columns=columns(item_rows))
    edited_items = st.data_editor(
        items_table,
        disabled=["file", "item", "check"],
        hide_index=True,
        width="stretch",
        key="items-" + editor_key,
    )
    final_items = edited_items.astype(object).where(edited_items.notna(), None).to_dict("records")

downloads = st.columns(4 if final_items else 3)
downloads[0].download_button("Download Excel", to_xlsx(final, final_items), "documents.xlsx",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
downloads[1].download_button("Download CSV", to_csv(final), "documents.csv", "text/csv")
downloads[2].download_button("Download JSON", to_json(final, final_items), "documents.json", "application/json")
if final_items:
    downloads[3].download_button("Download line items CSV", to_csv(final_items), "line_items.csv", "text/csv")

# ---------------------------------------------------------------- one document

st.subheader("Look at one document")
position = st.selectbox("Document", range(len(done)), format_func=lambda i: done[i][1]["file"])
index, item = done[position]

if item["result"] is None:
    st.error(item["error"])
    st.stop()

result = item["result"]
left, right = st.columns([1, 1])

with right:
    for number, (field, value) in enumerate(result["fields"].items()):
        colour = COLOURS[number % len(COLOURS)]
        note = result["review"].get(field)
        flag = f" — <span style='color:#b45309'>{note}</span>" if note else ""
        st.markdown(f"<span style='color:{colour};font-weight:600'>■ {field}</span>{flag}",
                    unsafe_allow_html=True)
        st.code(value if value else "(not found)", language=None)

    line_items = result.get("line_items")
    if line_items:
        st.markdown("**Line items** (experimental)")
        if line_items["items"]:
            st.dataframe(
                pandas.DataFrame(line_items["items"], columns=["name", "quantity", "unit_price", "amount"]),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("No items found.")
        sums = [f"{label} {line_items[key]}" for key, label in
                (("subtotal", "subtotal"), ("tax", "tax"), ("service", "service"), ("discount", "discount"), ("total", "total"))
                if line_items[key] is not None]
        if sums:
            st.caption(" · ".join(sums))
        gate = result.get("gate")
        # A rule set's gate checks the fields, not the items: their own reasons stand.
        item_reasons = ([reason.removeprefix("line items: ") for reason in gate["reasons"] if reason.startswith("line items: ")]
                        if gate is not None and not result.get("template")
                        else line_items["reasons"] if line_items["needs_review"] else [])
        if item_reasons:
            st.warning("Check the items: " + "; ".join(item_reasons))
        if gate is not None and gate["accepted"] and not item_reasons:
            st.success("Every check passed: accepted without a look.")

    if result.get("warning"):
        st.warning(result["warning"])
    chain = result.get("chain", [])
    if len(chain) > 1:
        answered = [step for step in chain if not step["error"]][-1]
        steps = [f"{step['method']} failed ({step['error']})" if step["error"]
                 else f"{step['method']} sent it on ({'; '.join(step['reasons']) or 'not checked'})"
                 for step in chain if step is not answered]
        last = f"{answered['method']} answered" + ("" if answered["accepted"] else ", but not every check passed")
        st.caption("Engines tried: " + " → ".join(steps + [last]))
    engine_name = f"rule set {result['template']}" if result.get("template") else f"engine {result['method']}"
    facts = [f"{result['seconds']:.1f} s", engine_name]
    if result["usd"] is not None:
        facts.append(f"${result['usd']:.5f}")
    if result["usage"]:
        facts.append(f"{result['usage']['input_tokens']} in / {result['usage']['output_tokens']} out tokens")
    st.caption(" · ".join(facts))
    if result["pages"] > 1:
        st.warning(f"Only page 1 of {result['pages']} was read.")

    with st.expander("Normalized values (dates as YYYY-MM-DD, amounts with two decimals)"):
        st.json(result["normalized"])
    if result["lines"]:
        with st.expander("OCR lines"):
            st.text("\n".join(line["text"] for line in result["lines"]))

with left:
    image = open_document(documents[index][1])
    if result["lines"]:
        st.image(draw_fields(image, result), caption="OCR lines behind each field, in the field's colour")
    else:
        st.image(image, caption="This engine reads the image directly, so there are no OCR boxes to outline.")
