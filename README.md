# Scanned Document Extractor

Extracts the company, address, date and total from scanned receipts, and
measures how well each approach works: classic OCR with rules, a CNN OCR, a
cloud vision model and local models that keep the document on the machine.

![UI: two uploaded receipts, their fields in an editable table, Excel, CSV and JSON downloads](docs/ui.jpg)

The receipt in the screenshot is synthetic ([docs/make_sample_receipt.py](docs/make_sample_receipt.py));
dataset images are not part of this repository.

## Use it

```bash
cp .env.example .env              # add OPENAI_API_KEY for the high-accuracy mode
docker compose up --build -d      # UI on http://localhost:8501, API on :8000
```

On first opening, the UI asks how the private mode's local model should run
and downloads it (about 2 GB, once) into the Ollama server. Until then the
private mode runs OCR and rules (E2). See [Private mode on a GPU](#private-mode-on-a-gpu).

1. Pick a mode. **High accuracy** sends the image to OpenAI (0.797 field accuracy,
   $0.31 per 1,000 receipts). **Private** runs on the server and nothing leaves it
   (free): a local vision model, E5 (0.825, about 21 s per receipt on the GPU).
   While no Ollama server is reachable it runs OCR and rules, E2 (0.631, 3.6 s),
   and says so. Without an OpenAI key only the private mode is offered.
2. Upload one or more receipt images or PDFs (first page is read, 10 MB each).
   The button shows the estimated cost before anything is sent.
3. Check the table. The *check* column flags fields that were not found, dates
   that are not dates and totals that are not amounts; edit any cell to correct it.
4. In the private mode a second table lists every purchased item with its amount
   (experimental, see [Line items](#line-items-cord)); a receipt whose items,
   subtotal, tax and total do not add up says why in *check*.
5. Download the tables, with your corrections, as Excel (line items on a second
   sheet), CSV or JSON. Rows to check are highlighted in the Excel file.
6. Pick a document to see it next to its fields and items; with OCR engines, the
   lines each field came from are outlined in the field's colour.

The modes are defined in [profiles.yaml](profiles.yaml); the numbers next to them
are read from the evaluation reports. Under *Advanced* any engine (e1 ... e5) can be
chosen directly, and the providers not built yet are listed as planned.

## Private mode on a GPU

The local model runs wherever the Ollama server runs. `docker compose up` starts
one on the CPU: nothing to install, about 25 to 35 s per receipt on a laptop.
**The GPU is used only when Ollama runs where it can reach the GPU:**

- **NVIDIA GPU:** needs a recent NVIDIA driver with CUDA (`nvidia-smi` should
  list the card); on Linux also the NVIDIA Container Toolkit. Then
  `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`.
- **Intel or AMD GPU:** Docker cannot use these. Install Ollama on the host
  (ollama.com); for an Intel integrated GPU set `OLLAMA_IGPU_ENABLE=1` and
  restart Ollama. Put `OLLAMA_HOST=http://host.docker.internal:11434` in `.env`
  and run `docker compose up -d`.

How much faster: on the development laptop's Intel Arc 130T integrated GPU, 12 s
per receipt against 33 s on its CPU, about 2.8 times (21 s when it also reads the
line items). A dedicated NVIDIA card should be faster still; not measured. The
UI's sidebar says whether the model ran on the GPU or the CPU.

## Results (400 held-out SROIE receipts)

| Pipeline | Field accuracy | Ignoring spacing | CER | s/receipt (CPU) | USD per 1,000 | Leaves the machine |
|---|---|---|---|---|---|---|
| E1 Tesseract + rules (baseline) | 0.464 | 0.498 | 0.465 | 1.34 | 0 | no |
| E2 RapidOCR (PP-OCRv6) + rules | 0.631 | 0.686 | 0.364 | 3.57 | 0 | no |
| E3 OpenAI gpt-6-luna, image to JSON | 0.797 | 0.858 | - | 7.16 | 0.31 | yes |
| E4 RapidOCR + local LLM (qwen3.5:4b) | 0.744 | 0.827 | 0.364 | 10.3 (iGPU) | 0 | no |
| E5 local vision model (qwen3-vl:2b-instruct) | **0.825** | **0.880** | - | 21.0 (iGPU) | 0 | no |

Field accuracy is the share of fields (company, address, date, total) that match
the label exactly after normalization (case, spaces, date format, amount format).
"Ignoring spacing" also accepts a value whose only difference from the label is
spacing or punctuation, because the labels themselves write the same address
both ways. Per field, per error kind: [evaluation/results/comparison.md](evaluation/results/comparison.md).

E4 and E5 were measured with Ollama for Windows on the laptop's Intel Arc 130T
iGPU (Vulkan, `OLLAMA_IGPU_ENABLE=1`); on its CPU they take about 2.8 times as
long. E5 also reads the line items in the same call, which its time includes.
See [docs/local-runs.md](docs/local-runs.md).

Measured on an Intel Core Ultra 5 226V; E1 to E3 on the CPU only. OpenAI prices as published on
2026-10-31.

## What the numbers say

- **A better OCR is the cheapest gain.** Swapping Tesseract for RapidOCR, with the
  same rules, takes accuracy from 0.464 to 0.631 and date errors from 102 to 16.
- **Rules cannot read layout.** Given the dataset's own error-free transcripts, the
  same rules reach only 0.704: the company and address need an understanding of
  the receipt that a model brings (E3: 0.797).
- **Totals and dates are solved; names and addresses are not.** E3 gets the total
  wrong on 9 of 400 receipts and the date on 10, the company on 99 and the address
  on 206 (81 of those only in spacing).
- **The cloud model can invent text.** Where the print is faded it once wrote a
  plausible but wrong company name. Low-quality scans should go to a human check.

Details: [evaluation/results/error_analysis.md](evaluation/results/error_analysis.md).

## How it works

```
image ─► OCR (Tesseract | RapidOCR) ─► lines + boxes ─► fields (rules | local LLM)
image ─────────────────────────────────────────────────► fields (OpenAI vision | local vision model)
```

- **Document types** ([extractor/doctypes](extractor/doctypes)): the fields to
  extract, their type (text, date, amount) and a description. The model prompts
  and JSON schemas are built from this file, so a new document type is a new
  YAML file, not new code.
- **Engines** ([extractor/engines](extractor/engines)): one YAML file per
  pipeline (OCR engine, field extractor, model, prices) with its measured results.
- **Modes** ([profiles.yaml](profiles.yaml)): what the user picks; each mode runs
  one engine.
- **Evaluation** ([evaluation](evaluation)): how the numbers were measured. Loads
  the dataset, runs an engine on it, reports field accuracy, CER, speed, tokens
  and cost per receipt; runs resume from a checkpoint if they stop. The product
  does not depend on it, and it is left out of the Docker images.

## Reproduce the measurements

```bash
bash scripts/download_sroie.sh              # SROIE with corrected labels, into data/
cp .env.example .env                        # add OPENAI_API_KEY for e3
docker compose --profile test run --rm test python -m evaluation.run --config e2
docker compose --profile test run --rm test python -m evaluation.compare
docker compose --profile test run --rm test # unit tests
```

## API

```
GET  /profiles                 modes, whether each can run here, measured accuracy and cost
GET  /methods                  every engine (e1 ... e5) with the same details
POST /extract  file            image or PDF; optional profile=accurate|private
               [profile|method]  or method=e1...e5 (default: the default mode)
               -> fields, normalized fields, fields to check, OCR lines and boxes,
                  the lines behind each field, line items (e2, e5; null otherwise),
                  seconds, tokens, USD, page count
```

`line_items` holds the items (name, quantity, unit price, amount), the subtotal,
tax, service, discount and total, and `needs_review` with the reasons. Amounts are
plain decimal strings ("43636", "9999.99"). `/methods` and `/profiles` say which
engines and modes return line items.

`gate` (with line items; null otherwise) says whether the result passed every
check and can be used without a look (`accepted`), and if not, why (`reasons`:
field notes, line-item reasons, an item that looks like an add-on). Documents it
does not accept are the ones to send on to a model or a person. On CORD it
accepts about half of the receipts read end to end, and about 1 in 40 of those is
still wrong: [reports/cord_gate.md](reports/cord_gate.md).

An engine that cannot run on the server (no OpenAI key, no Ollama) is answered
with 503 and the reason.

## Data

[SROIE](https://rrc.cvc.uab.es/?ch=13) (ICDAR 2019) receipts with the corrected
labels from [zzzDavid/ICDAR-2019-SROIE](https://github.com/zzzDavid/ICDAR-2019-SROIE).
That folder holds the 626 labelled training receipts; the official test labels
are not in it. They are split once with a fixed seed into 400 test receipts,
used only for the reported numbers, and 226 dev receipts, used to write the
rules and the field descriptions. The 400 are not the official SROIE test set.

### Line items (CORD)

E2 also returns line items, read by rules ([extractor/fields/line_items.py](extractor/fields/line_items.py)):
every purchased item with its amount, the subtotal, tax, service, discount and
total, and a `needs_review` flag when the numbers do not add up. The receipt
document type declares them (`line_items: true`) and an engine reads them when its
YAML says how: `line_items: rules` (E2, from the OCR boxes) or `line_items: model`
(E5: the vision model is asked for the items in the same call as the fields;
its answer is parsed and reconciled like the rules'). For E2, OCR runs once: the rules get
the boxes as detected, the field rules the same boxes joined into lines as before,
so E2's field results do not change. It is measured
on [CORD v2](https://huggingface.co/datasets/naver-clova-ix/cord-v2)
(Park et al., *CORD: A Consolidated Receipt Dataset for Post-OCR Parsing*,
Document Intelligence Workshop at NeurIPS 2019; CC-BY-4.0), 1,000 Indonesian
receipts. Results and error analysis: [reports/cord_line_items.md](reports/cord_line_items.md).

```bash
bash scripts/download_cord.sh                                   # 2.3 GB, into data/cord/
python -m evaluation.cord_eval --mode oracle --split validation # dataset words as OCR
python -m evaluation.cord_eval --mode e2e --split validation    # RapidOCR on the image
python -m evaluation.cord_gate --mode e2e --split validation    # the review gate on those results
OLLAMA_HOST=http://localhost:11434 python -m evaluation.cord_eval --mode e5 --split validation --threads 8
python -m evaluation.cord_gate --hybrid --split validation       # E2, then E5 for what E2's gate rejects
```

## Limitations

- Tested on printed English receipts from one country (Malaysia). Handwriting,
  multi-page documents and other document types are not measured yet.
- The highlighted boxes in the UI mark every OCR line that contains a value; when a
  subtotal equals the total, both lines are outlined.
- The *check* column only catches values that are missing or malformed. A
  well-formed but wrong value, such as a misread company name, is not flagged.
- Only the first page of a PDF is read.
- E5's line items are measured on CORD, not SROIE: [reports/cord_e5_line_items.md](reports/cord_e5_line_items.md).
- Line items are measured on Indonesian receipts (CORD), not on the Malaysian
  ones the fields are measured on (SROIE has no item labels). About half of the
  CORD receipts read end to end are flagged for review. E2 and E5 return them.
- The field rules were written for Malaysian receipts; on other receipts the
  fields' total and the line items' total can disagree. Both are reported as
  read, and *check* says the total differs from the line items' total.
