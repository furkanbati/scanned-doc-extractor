# e3: OpenAI gpt-6-luna reads the image and returns the fields as JSON (no OCR).

- **config:** e3
- **description:** OpenAI gpt-6-luna reads the image and returns the fields as JSON (no OCR).
- **doctype:** receipt
- **date:** 2026-10-31 10:04 UTC
- **split:** test
- **ocr:** none
- **fields:** openai_vision (gpt-6-luna)
- **cpu:** Intel(R) Core(TM) Ultra 5 226V
- **Receipts:** 400 (0 failed, counted as empty)

## Field accuracy

Normalized exact match per field (see `extractor/schema.py`).

| Source | company | address | date | total | Mean |
|---|---|---|---|---|---|
| Pipeline | 0.752 | 0.485 | 0.975 | 0.978 | 0.797 |

## OCR, speed and cost

| Metric | Value |
|---|---|
| Seconds per receipt | 7.16 |
| Input tokens per receipt | 2218 |
| Output tokens per receipt (incl. reasoning) | 178 |
| USD per receipt | 0.00031 |
| USD per 1,000 receipts | 0.31 |
