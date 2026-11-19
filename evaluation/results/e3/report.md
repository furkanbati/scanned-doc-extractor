# e3: OpenAI gpt-6-luna reads the image and returns the fields as JSON (no OCR).

- **config:** e3
- **description:** OpenAI gpt-6-luna reads the image and returns the fields as JSON (no OCR).
- **doctype:** receipt
- **date:** 2026-11-19 17:04 UTC
- **split:** test
- **ocr:** none
- **fields:** openai_vision (gpt-6-luna)
- **cpu:** Intel64 Family 6 Model 197 Stepping 2, GenuineIntel
- **Receipts:** 400 (0 failed, counted as empty)

## Field accuracy

Normalized exact match per field (see `extractor/schema.py`).

| Source | company | address | date | total | Mean |
|---|---|---|---|---|---|
| Pipeline | 0.760 | 0.480 | 0.980 | 0.983 | 0.801 |

## OCR, speed and cost

| Metric | Value |
|---|---|
| Seconds per receipt | 11.72 |
| Input tokens per receipt | 2486 |
| Output tokens per receipt (incl. reasoning) | 528 |
| USD per receipt | 0.00051 |
| USD per 1,000 receipts | 0.51 |
