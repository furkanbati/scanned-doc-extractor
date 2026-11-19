# e5: A local vision model (Ollama) reads the image and returns the fields as JSON (no OCR).

- **config:** e5
- **description:** A local vision model (Ollama) reads the image and returns the fields as JSON (no OCR).
- **doctype:** receipt
- **date:** 2026-11-19 12:27 UTC
- **split:** test
- **ocr:** none
- **fields:** local_vision (qwen3-vl:2b-instruct)
- **cpu:** Intel64 Family 6 Model 197 Stepping 2, GenuineIntel
- **Receipts:** 400 (0 failed, counted as empty)

## Field accuracy

Normalized exact match per field (see `extractor/schema.py`).

| Source | company | address | date | total | Mean |
|---|---|---|---|---|---|
| Pipeline | 0.860 | 0.568 | 0.993 | 0.880 | 0.825 |

## OCR, speed and cost

| Metric | Value |
|---|---|
| Seconds per receipt | 20.97 |
| Input tokens per receipt | 1645 |
| Output tokens per receipt (incl. reasoning) | 296 |
