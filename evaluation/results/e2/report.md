# e2: RapidOCR (PP-OCRv6 CNNs) on the raw image, rule and regex fields.

- **config:** e2
- **description:** RapidOCR (PP-OCRv6 CNNs) on the raw image, rule and regex fields.
- **doctype:** receipt
- **date:** 2026-10-31 09:06 UTC
- **split:** test
- **ocr:** rapidocr 3.10.0 (PP-OCRv6 small)
- **cpu:** Intel(R) Core(TM) Ultra 5 226V
- **Receipts:** 400

## Field accuracy

Normalized exact match per field (see `extractor/schema.py`).

| Source | company | address | date | total | Mean |
|---|---|---|---|---|---|
| OCR | 0.477 | 0.345 | 0.960 | 0.740 | 0.631 |
| Labelled lines | 0.632 | 0.507 | 0.990 | 0.688 | 0.704 |

"Labelled lines" runs the same field extractor on the dataset's line transcripts instead of OCR output: the gap to the OCR row is what OCR errors cost.

## OCR and speed

| Metric | Value |
|---|---|
| CER (corpus) | 0.364 |
| Seconds per receipt (CPU) | 3.57 |

CER compares OCR lines joined in the engine's reading order with the labelled lines joined in annotation order.
