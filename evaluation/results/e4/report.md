# e4: RapidOCR (PP-OCRv6) lines, a local LLM (Ollama) returns the fields as JSON.

- **config:** e4
- **description:** RapidOCR (PP-OCRv6) lines, a local LLM (Ollama) returns the fields as JSON.
- **doctype:** receipt
- **date:** 2026-11-19 13:36 UTC
- **split:** test
- **ocr:** rapidocr 3.10.0 (PP-OCRv6 small)
- **fields:** local_llm (qwen3.5:4b)
- **cpu:** Intel64 Family 6 Model 197 Stepping 2, GenuineIntel
- **Receipts:** 400 (0 failed, counted as empty)

## Field accuracy

Normalized exact match per field (see `extractor/schema.py`).

| Source | company | address | date | total | Mean |
|---|---|---|---|---|---|
| Pipeline | 0.672 | 0.405 | 0.975 | 0.925 | 0.744 |

## OCR, speed and cost

| Metric | Value |
|---|---|
| CER (corpus) | 0.364 |
| Seconds per receipt | 10.31 |
| Input tokens per receipt | 639 |
| Output tokens per receipt (incl. reasoning) | 92 |

CER compares OCR lines joined in the engine's reading order with the labelled lines joined in annotation order.
