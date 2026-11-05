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
from PIL import Image, ImageDraw

from export import columns, result_rows, to_csv, to_json, to_xlsx

API_URL = os.getenv("API_URL", "http://localhost:8000")
PDF_DPI = 200  # the API renders PDFs at the same scale, so boxes line up

# One colour per field position; fields beyond the palette reuse it.
COLOURS = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed", "#0891b2"]
BOOKKEEPING = ["file", "check", "seconds", "usd"]

st.set_page_config(page_title="Scanned Document Extractor", page_icon="🧾", layout="wide")


@st.cache_data(ttl=30)
def load_catalogue() -> tuple[dict, dict]:
    profiles = requests.get(f"{API_URL}/profiles", timeout=10)
    profiles.raise_for_status()
    engines = requests.get(f"{API_URL}/methods", timeout=10)
    engines.raise_for_status()

    return profiles.json(), engines.json()["methods"]


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

        st.markdown("**Planned providers**")
        for provider in catalogue["planned"]:
            st.markdown(f"<span style='opacity:0.45'>○ {provider} — planned</span>", unsafe_allow_html=True)

    if leaves:
        st.warning("Documents are sent to a cloud API (OpenAI) in this mode.")
    if not available:
        st.error(f"Not available on this server: {reason}")

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

summary = st.columns(4)
summary[0].metric("Documents", len(items))
summary[1].metric("To check", sum(1 for row in rows if row["check"]))
summary[2].metric("Time", f"{sum(item['result']['seconds'] for item in ok):.1f} s")
summary[3].metric("Cost", f"${sum(item['result']['usd'] or 0 for item in ok):.4f}")

st.subheader("Fields")
st.caption("Edit any cell to correct it; downloads include your corrections. "
           "The check column lists what looks wrong; a misread name can still look right.")

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
    # A new set of documents starts a fresh table, so edits never land on the wrong row.
    key="editor-" + hashlib.sha1(repr([key for key in keys if key in results]).encode()).hexdigest()[:12],
)
final = edited.astype(object).where(edited.notna(), None).to_dict("records")

downloads = st.columns(3)
downloads[0].download_button("Download Excel", to_xlsx(final), "documents.xlsx",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
downloads[1].download_button("Download CSV", to_csv(final), "documents.csv", "text/csv")
downloads[2].download_button("Download JSON", to_json(final), "documents.json", "application/json")

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

    facts = [f"{result['seconds']:.1f} s", f"engine {result['method']}"]
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
