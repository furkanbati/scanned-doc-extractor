# Comparison

SROIE test split, 400 receipts. Field accuracy is the normalized exact match (`extractor/schema.py`). "Ignoring spacing" also counts a value as correct when only spaces or punctuation differ from the label, because the labels themselves are not consistent there (`TAMPOI, 81200` next to `TAMPOI,81200`).

## Results

E4 and E5 ran on the Intel Arc 130T iGPU through Ollama (Vulkan), the others on the CPU.

| Experiment | company | address | date | total | Mean | Mean, ignoring spacing | s/receipt | USD per 1,000 | Leaves the machine |
|---|---|---|---|---|---|---|---|---|---|
| e1: Baseline. Tesseract on the raw image, rule and regex fields. | 0.355 | 0.190 | 0.745 | 0.565 | **0.464** | 0.498 | 1.34 | 0 | no |
| e2: RapidOCR (PP-OCRv6 CNNs) on the raw image, rule and regex fields. | 0.477 | 0.345 | 0.960 | 0.740 | **0.631** | 0.686 | 3.57 | 0 | no |
| e3: OpenAI gpt-6-luna reads the image and returns the fields and line items as JSON (no OCR). | 0.760 | 0.480 | 0.980 | 0.983 | **0.801** | 0.863 | 11.72 | 0.51 | yes |
| e4: RapidOCR (PP-OCRv6) lines, a local LLM (Ollama) returns the fields as JSON. | 0.672 | 0.405 | 0.975 | 0.925 | **0.744** | 0.827 | 10.31 | 0 | no |
| e5: A local vision model (Ollama) reads the image and returns the fields as JSON (no OCR). | 0.860 | 0.568 | 0.993 | 0.880 | **0.825** | 0.880 | 20.97 | 0 | no |

## What kind of errors

Every field that does not match its label, by kind: **format** (same letters and digits, only spacing or punctuation differ), **near** (at most 2 letters or digits off: a misread character, or a typo in the label), **missing** (nothing extracted) and **wrong** (anything else).

| Experiment | Field | format | near | missing | wrong | Errors |
|---|---|---|---|---|---|---|
| e1 | company | 11 | 64 | 8 | 175 | 258 |
| e1 | address | 44 | 59 | 40 | 181 | 324 |
| e1 | date | 0 | 24 | 76 | 2 | 102 |
| e1 | total | 0 | 62 | 23 | 89 | 174 |
| e2 | company | 16 | 18 | 0 | 175 | 209 |
| e2 | address | 73 | 34 | 51 | 104 | 262 |
| e2 | date | 0 | 0 | 15 | 1 | 16 |
| e2 | total | 0 | 44 | 0 | 60 | 104 |
| e3 | company | 16 | 32 | 0 | 48 | 96 |
| e3 | address | 83 | 44 | 0 | 81 | 208 |
| e3 | date | 0 | 7 | 0 | 1 | 8 |
| e3 | total | 0 | 6 | 0 | 1 | 7 |
| e4 | company | 28 | 33 | 0 | 70 | 131 |
| e4 | address | 103 | 65 | 1 | 69 | 238 |
| e4 | date | 0 | 5 | 0 | 5 | 10 |
| e4 | total | 1 | 21 | 0 | 8 | 30 |
| e5 | company | 6 | 14 | 0 | 36 | 56 |
| e5 | address | 82 | 43 | 0 | 48 | 173 |
| e5 | date | 0 | 3 | 0 | 0 | 3 |
| e5 | total | 0 | 28 | 0 | 20 | 48 |

## Hardest receipts for e3

From the fields-only run of e3 (2026-10-31), then the most accurate experiment: 57 receipts have at least 2 fields wrong in e3. Receipt ids refer to `data/sroie`.

| Receipt | company | address | date | total |
|---|---|---|---|---|
| 505 | wrong | near | near | near |
| 057 | near | format | near |  |
| 314 | wrong | wrong | near |  |
| 000 | format | near |  |  |
| 026 | near | format |  |  |
| 031 |  | near | near |  |
| 036 | near | format |  |  |
| 039 | wrong | wrong |  |  |
| 046 | near | format |  |  |
| 049 |  | near | near |  |
| 058 | near | format |  |  |
| 070 |  | wrong | wrong |  |
| 077 | wrong | format |  |  |
| 096 |  | wrong |  | near |
| 146 |  | wrong |  | near |
| 149 |  | near |  | near |
| 155 | wrong | wrong |  |  |
| 165 | near | near |  |  |
| 172 | format | wrong |  |  |
| 183 |  | format | near |  |
