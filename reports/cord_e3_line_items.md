# Line items by OpenAI (E3) on CORD, alone and after E2

E3 asks OpenAI's vision model for the purchased items, their add-ons and the
sums below them, in the same call as the fields. The prompt and the JSON
schema are E5's ([extractor/fields/model_items.py](../extractor/fields/model_items.py)),
and the answer goes through the same amount parser, reconciliation and review
gate as E2's and E5's, so the three are measured the same way.

- Model: `gpt-6-luna`, reasoning effort low, image detail high, strict JSON
  schema. The image is sent to OpenAI; CORD is a public dataset.
- Metrics, labels and the split rules are those of
  [reports/cord_line_items.md](cord_line_items.md); the gate is that of
  [reports/cord_gate.md](cord_gate.md).

**Test split: run once**, on 2026-11-19, with the prompt and code frozen at
commit `b0f04b1`. Nothing was changed between the validation run and the test
run. Train was not run.

## E3 alone, against E2 and E5

| Split | Engine | Required values | Receipts fully right | needs_review | Silent errors | Amount F1 | s/receipt | USD per 1,000 |
|---|---|---|---|---|---|---|---|---|
| validation | E2 (RapidOCR + rules) | 0.905 | 0.76 | 0.38 | 0.04 | 0.908 | 1.6 | 0 |
| validation | E5 (local vision model) | 0.912 | 0.78 | 0.57 | 0.01 | 0.877 | 27.0 | 0 |
| validation | E3 (OpenAI) | 0.979 | 0.95 | 0.25 | 0.02 | 0.980 | 5.7 | 0.39 |
| **test** | E2 (RapidOCR + rules) | 0.767 | 0.62 | 0.53 | 0.03 | 0.861 | 1.7 | 0 |
| **test** | E5 (local vision model) | 0.823 | 0.64 | 0.59 | 0.03 | 0.866 | 26.4 | 0 |
| **test** | **E3 (OpenAI)** | **0.922** | **0.87** | **0.26** | 0.04 | **0.929** | 6.9 | 0.40 |

- E3 is clearly the best reader of line items: on test, 87 receipts of 100
  fully right against 62 (E2) and 64 (E5), and half as many flagged for review.
- Its errors are mostly extra rows (5 receipts): lines the label does not
  count as items, such as printed separators.
- It costs about $0.40 per 1,000 receipts with the items, against $0.31 for
  the fields alone on SROIE (more output tokens).

## E3 behind the review gate

| Split | Gate | Accepted | Wrong among accepted (95% CI) |
|---|---|---|---|
| validation | standard | 75 | 2/75 = 0.027 (0.007-0.092) |
| validation | strict | 68 | 2/68 = 0.029 (0.008-0.101) |
| **test** | **standard** | **73** | 3/73 = 0.041 (0.014-0.114) |
| test | strict | 63 | 1/63 = 0.016 (0.003-0.085) |

Of the 3 wrong receipts the standard gate accepted on test, one is a real
misreading and two are label conventions:

- `test-0020`: E3 read 64.500 as 64.00 on several lines; its own numbers still
  added up, so the gate let it through.
- `test-0026`: the label has no price for "Teh Tawar"; E3 read 8,000, and the
  total matches E3's items.
- `test-0070`: the label counts the separators "=\*LARGE\*==" and
  "=\*MEDIUM\*=" as items; E3, like E5, left them out.

## The chain: E2, then E3 for what E2's gate rejects, then a person

| Split | Gate | Accepted by E2 | Sent to E3 | Accepted by E3 | To a person | Wrong among accepted (95% CI) | s/receipt | USD per 1,000 |
|---|---|---|---|---|---|---|---|---|
| validation | standard | 61 (3 wrong) | 39 | 20 (1 wrong) | 19 | 4/81 = 0.049 (0.019-0.120) | 3.8 | 0.15 |
| validation | strict | 56 (2 wrong) | 44 | 16 (1 wrong) | 28 | 3/72 = 0.042 (0.014-0.115) | 4.1 | 0.17 |
| **test** | **standard** | 46 (2 wrong) | 54 | **30 (3 wrong)** | **24** | 5/76 = 0.066 (0.028-0.145) | 5.4 | 0.21 |
| test | strict | 41 (0 wrong) | 59 | 24 (1 wrong) | 35 | 1/65 = 0.015 (0.003-0.082) | 5.7 | 0.23 |

Against the E2 → E5 chain on test (standard gate: 56 accepted, 44 to a person):

- E3 as the second step accepts 30 of the 54 receipts E2 rejects, E5 only 10.
  The share handled without a person goes from 56% to 76%.
- It costs about $0.21 per 1,000 receipts, because only the rejected half is
  sent, and it is faster (5 s against 16 s per receipt on average).
- The strict gate keeps 65 receipts automatic with 1 wrong among them.

## What this means

- **Where the image may leave the machine, E3 is the line-item reader to use**:
  it reads items far better than E2 and E5, for well under a cent per receipt.
- **E2 first, E3 for what E2 rejects** gives most of E3's automation at about
  half its cost, and keeps half the receipts on the machine.
- **E3 alone, behind the gate, accepts about as many receipts as the chain**
  (73 against 76 on test) at twice the cost per receipt; the chain wins on
  cost, E3 alone on simplicity.

## Reproduce

```bash
# OPENAI_API_KEY in the environment; costs about $0.04 per 100 receipts
python -m evaluation.cord_eval --mode e3 --split validation
python -m evaluation.cord_gate --mode e3 --split validation
python -m evaluation.cord_gate --hybrid --second e3 --split validation
python -m evaluation.cord_gate --table
```

Answers and their tokens are cached in `data/cord/e3/<split>.jsonl`, so a
rerun only parses them and costs nothing.
