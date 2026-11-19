# Line items by a local vision model (E5) on CORD, alone and after E2

E5 asks a local vision model for the purchased items, their add-ons and the
sums below them, in the same call as the fields. The answer goes through the
same amount parser and the same reconciliation as E2's rules, so
`needs_review` means the same for both: the numbers do not add up. The review
gate ([reports/cord_gate.md](cord_gate.md)) then decides, the same way for both.

- Model: `qwen3-vl:2b-instruct` through Ollama, CPU only, 8 threads, images
  shrunk to 1,600 px on the long side. Everything runs on the machine.
- Prompt and parsing: [extractor/fields/model_items.py](../extractor/fields/model_items.py).
- Metrics, labels and the split rules are those of
  [reports/cord_line_items.md](cord_line_items.md).

**Test split: run once**, on 2026-11-18, with the prompt frozen at commit
`0a32b42`. The prompt was written before any receipt was read and was not
changed after the validation run. Train (800 receipts, about 6 hours on this
CPU) was not run.

## Two fixes before anything could be measured

- `qwen3-vl:2b`, the model E5 was configured with, is a thinking model. With
  `think: false` it still spent the whole 2,048-token budget thinking and
  returned an empty answer. E5 now uses `qwen3-vl:2b-instruct`. Until this
  change E5 returned nothing, which is likely why it had not been measured.
- Ollama's default of one thread per core gave 2.8 tokens/s in Docker on this
  14-core laptop; 8 threads gave 43. One receipt went from over 15 minutes to
  about 26 seconds. The thread count is now an option (`threads`).

## E5 alone, against E2

| Split | Engine | Required values | Receipts fully right | needs_review | Silent errors | Amount F1 | s/receipt |
|---|---|---|---|---|---|---|---|
| validation | E2 (RapidOCR + rules) | 0.905 | 0.76 | 0.38 | 0.04 | 0.908 | 1.6 |
| validation | E5 (vision model) | 0.912 | 0.78 | 0.57 | 0.01 | 0.877 | 27.0 |
| **test** | E2 (RapidOCR + rules) | 0.767 | 0.62 | 0.53 | 0.03 | 0.861 | 1.7 |
| **test** | **E5 (vision model)** | **0.823** | **0.64** | 0.59 | 0.03 | 0.866 | 26.4 |

- Accuracy is close. On test E5 is ahead on required values (it misses fewer
  rows: 7 against 14), on validation the two are level.
- E5 flags more receipts, and its own numbers less often add up.
- E5 is about 16 times slower on this CPU, and free.

## The chain: E2, then E5 for what E2's gate rejects, then a person

| Split | Gate | Accepted by E2 | Sent to E5 | Accepted by E5 | To a person | Wrong among accepted (95% CI) | s/receipt |
|---|---|---|---|---|---|---|---|
| validation | standard | 61 (3 wrong) | 39 | 8 (1 wrong) | 31 | 4/69 = 0.058 (0.023-0.140) | 12.1 |
| validation | strict | 56 (2 wrong) | 44 | 6 (0 wrong) | 38 | 2/62 = 0.032 (0.009-0.110) | 13.5 |
| **test** | **standard** | 46 (2 wrong) | 54 | **10 (2 wrong)** | **44** | 4/56 = 0.071 (0.028-0.170) | 15.9 |
| test | strict | 41 (0 wrong) | 59 | 7 (2 wrong) | 52 | 2/48 = 0.042 (0.012-0.140) | 17.2 |

- E5 accepts about one in five of the receipts E2 rejects: the automatic share
  goes from 46% to 56% on test (61% to 69% on validation).
- Most of what E2 rejects, E5 rejects too. The hard receipts are hard for both:
  add-ons, free lines, totals that do not match the items.
- The time per receipt is E2 on every receipt plus E5 on the ones sent to it.

The 2 wrong receipts E5 accepted on test are both label conventions rather
than misreadings. In `test-0012` the label lists "BLUS WANITA" and "SHOPPING
BAG" with no prices and one total; E5 read one priced line. In `test-0070` the
label counts the printed separators "=\*LARGE\*==" and "=\*MEDIUM\*=" as items
without a price; E5 left them out, and every amount and the total are right.

## What this means

- **A small local model reads line items about as well as OCR plus rules**,
  without any rule written for receipts. But it is slow on a CPU, and it fails
  on the same receipts as the rules.
- **As a second step it adds about 10 points of automation** at no cost and
  without the image leaving the machine. Whether 25 seconds per rejected
  receipt is acceptable depends on volume; a GPU would make it a few seconds.
- **The receipts left for a person (31 to 52 in 100) need a stronger reader**,
  such as a larger local model on a GPU or, where allowed, E3, which does not
  read line items yet.

## Reproduce

```bash
docker compose up -d ollama   # publish port 11434 to reach it from the host
OLLAMA_HOST=http://localhost:11434 python -m evaluation.cord_eval --mode e5 --split validation --threads 8
python -m evaluation.cord_gate --mode e5 --split validation
python -m evaluation.cord_gate --hybrid --split validation
python -m evaluation.cord_gate --table
```

Answers are cached in `data/cord/e5/<split>.jsonl`, so a rerun only parses them.
