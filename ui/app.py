"""Streamlit interface: upload a document image, see the extracted fields.

Run with `streamlit run ui/app.py`; set API_URL to point at the API.
"""

import io
import os

import requests
import streamlit as st
from PIL import Image, ImageDraw

API_URL = os.getenv("API_URL", "http://localhost:8000")

# One colour per field position; fields beyond the palette reuse it.
COLOURS = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed", "#0891b2"]

st.set_page_config(page_title="Scanned Document Extractor", page_icon="🧾", layout="wide")


@st.cache_data(ttl=60)
def load_methods() -> dict:
    response = requests.get(f"{API_URL}/methods", timeout=10)
    response.raise_for_status()

    return response.json()


def extract(image_bytes: bytes, filename: str, method: str) -> dict:
    response = requests.post(
        f"{API_URL}/extract",
        files={"file": (filename, image_bytes)},
        data={"method": method},
        timeout=900,
    )
    if response.status_code != 200:
        raise RuntimeError(response.json().get("detail", response.text))

    return response.json()


def draw_fields(image: Image.Image, result: dict) -> Image.Image:
    """The image with each field's OCR lines outlined in the field's colour."""

    canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    width = max(2, canvas.width // 300)

    for position, (field, indexes) in enumerate(result["field_lines"].items()):
        colour = COLOURS[position % len(COLOURS)]
        for index in indexes:
            box = result["lines"][index]["box"]
            draw.polygon(list(zip(box[0::2], box[1::2])), outline=colour, width=width)

    return canvas


def method_label(name: str, method: dict) -> str:
    where = "cloud" if method["leaves_machine"] else "local"

    return f"{name} · {method['ocr']} → {method['fields']} ({where})"


st.title("Scanned Document Extractor")
st.caption("Upload a scanned receipt; the fields come back as structured data.")

try:
    catalogue = load_methods()
except requests.RequestException as error:
    st.error(f"API not reachable at {API_URL}: {error}")
    st.stop()

methods = catalogue["methods"]
names = list(methods)
method = st.sidebar.selectbox(
    "Method",
    names,
    index=names.index(catalogue["default"]) if catalogue["default"] in names else 0,
    format_func=lambda name: method_label(name, methods[name]),
)
st.sidebar.caption(methods[method]["description"])
if methods[method]["leaves_machine"]:
    st.sidebar.warning("This method sends the image to a cloud API.")

upload = st.file_uploader("Receipt image", type=["jpg", "jpeg", "png"])
if upload is None:
    st.stop()

image_bytes = upload.getvalue()
image = Image.open(io.BytesIO(image_bytes))

with st.spinner(f"Extracting with {method}..."):
    try:
        result = extract(image_bytes, upload.name, method)
    except (requests.RequestException, RuntimeError) as error:
        st.error(str(error))
        st.stop()

left, right = st.columns([1, 1])

with right:
    st.subheader("Fields")
    for position, (field, value) in enumerate(result["fields"].items()):
        colour = COLOURS[position % len(COLOURS)]
        st.markdown(
            f"<span style='color:{colour};font-weight:600'>■ {field}</span>",
            unsafe_allow_html=True,
        )
        st.code(value if value is not None else "(not found)", language=None)

    facts = [f"{result['seconds']:.1f} s"]
    if result["usd"] is not None:
        facts.append(f"${result['usd']:.5f}")
    if result["usage"]:
        facts.append(f"{result['usage']['input_tokens']} in / {result['usage']['output_tokens']} out tokens")
    st.caption(" · ".join(facts))

    with st.expander("Normalized values (as compared in the evaluation)"):
        st.json(result["normalized"])

with left:
    if result["lines"]:
        st.image(draw_fields(image, result), caption="OCR lines behind each field")
    else:
        st.image(image, caption="This method reads the image directly; no OCR boxes.")
