# Comparison

SROIE test split, 400 receipts. Field accuracy is the normalized exact match (`extractor/schema.py`). "Ignoring spacing" also counts a value as correct when only spaces or punctuation differ from the label, because the labels themselves are not consistent there (`TAMPOI, 81200` next to `TAMPOI,81200`).

## Results

| Experiment | company | address | date | total | Mean | Mean, ignoring spacing | s/receipt | USD per 1,000 | Leaves the machine |
|---|---|---|---|---|---|---|---|---|---|
| e1: Baseline. Tesseract on the raw image, rule and regex fields. | 0.355 | 0.190 | 0.745 | 0.565 | **0.464** | 0.498 | 1.34 | 0 | no |
| e2: RapidOCR (PP-OCRv6 CNNs) on the raw image, rule and regex fields. | 0.477 | 0.345 | 0.960 | 0.740 | **0.631** | 0.686 | 3.57 | 0 | no |
| e3: OpenAI gpt-6-luna reads the image and returns the fields as JSON (no OCR). | 0.752 | 0.485 | 0.975 | 0.978 | **0.797** | 0.858 | 7.16 | 0.31 | yes |

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
| e3 | company | 16 | 32 | 0 | 51 | 99 |
| e3 | address | 81 | 46 | 0 | 79 | 206 |
| e3 | date | 0 | 9 | 0 | 1 | 10 |
| e3 | total | 0 | 7 | 0 | 2 | 9 |

## Hardest receipts for e3

57 receipts have at least 2 fields wrong in e3, the most accurate experiment. Receipt ids refer to `data/sroie`.

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
