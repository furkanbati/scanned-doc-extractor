# The review gate on CORD: which receipts can skip the model

Step 1 of the hybrid plan: E2 (RapidOCR + rules, local, free) reads every
receipt and checks its own result. The gate ([extractor/gate.py](../extractor/gate.py))
accepts the result only when every check passes. Every other receipt goes on to
a model or a person. Two numbers matter:

- **Accepted**: the share of receipts that never reach a model. This is the
  saving.
- **Wrong among accepted**: the share of accepted receipts with at least one
  wrong required value (item count, an item amount, the total). Nobody looks at
  these, so this is the error rate of the data that comes out of the system
  unseen.

No model is called in this measurement. A receipt the gate rejects is only
counted as "goes to a model or a person"; what the model would make of it is
not measured here.

**Test split: run once**, on 2026-11-18, after the gate was frozen at commit
`022b1af`. The checks and the strict level were chosen on train and validation.
The line-item rules are unchanged since their own test run (commit `b3db1d0`).

## The checks

All are general properties of a receipt; none names a store or a layout.

| Check | Where it comes from |
|---|---|
| Every field's review note (missing, not a date, not an amount, a total that differs from the line items') | `extractor/review.py` |
| Every line-item reason: items, subtotal, tax and total do not add up; no TOTAL line; a total found only by arithmetic; an amount computed rather than read; a free line; folded modifiers | `needs_review` of the line-item rules |
| At least one item | gate |
| No item after the first whose name starts like an add-on: "Extra ...", "ADD ...", "+ ...", "Tambah ...", "Topping ..." | gate |
| *Strict level only:* no item that costs under a quarter of the item right above it | gate |

The add-on check comes from the error analysis on train. Of the 13 train
receipts that passed every line-item check and were still wrong, 10 counted a
topping ("PEARL", "Extra Jelly Lychee", "ADD 1 W. ORIGINAL") as an item. CORD
labels toppings as part of the item above. The sums add up either way, so the
arithmetic cannot tell the two apart; only the name or the price can.

On CORD the gate sees the line items only. CORD has no labels for company,
date or address, and E2's field rules were written for Malaysian receipts
(SROIE), so field notes would reject receipts for reasons that say nothing about
the items.

## Results

Wrong among accepted is shown with its 95% Wilson interval: on 50 accepted
receipts, one more or one fewer wrong receipt moves the rate by two points.

| Mode | Split | Gate | Receipts | Accepted | Wrong among accepted (95% CI) | Right receipts accepted | To model or person | E3 cost / 1000 |
|---|---|---|---|---|---|---|---|---|
| e2e | train | standard | 800 | 0.53 | 8/420 = 0.019 (0.010-0.037) | 0.72 | 0.47 | $0.148 |
| e2e | train | strict | 800 | 0.48 | 3/382 = 0.008 (0.003-0.023) | 0.66 | 0.52 | $0.162 |
| e2e | validation | standard | 100 | 0.61 | 3/61 = 0.049 (0.017-0.135) | 0.76 | 0.39 | $0.121 |
| e2e | validation | strict | 100 | 0.56 | 2/56 = 0.036 (0.010-0.121) | 0.71 | 0.44 | $0.137 |
| **e2e** | **test** | **standard** | 100 | **0.46** | **2/46 = 0.043 (0.012-0.145)** | 0.71 | 0.54 | $0.168 |
| e2e | test | strict | 100 | 0.41 | 0/41 = 0.000 (0.000-0.086) | 0.66 | 0.59 | $0.183 |
| oracle | train | standard | 800 | 0.64 | 6/515 = 0.012 (0.005-0.025) | 0.76 | 0.36 | $0.111 |
| oracle | train | strict | 800 | 0.57 | 3/456 = 0.007 (0.002-0.019) | 0.67 | 0.43 | $0.134 |
| oracle | validation | standard | 100 | 0.76 | 3/76 = 0.039 (0.014-0.110) | 0.84 | 0.24 | $0.075 |
| oracle | validation | strict | 100 | 0.69 | 2/69 = 0.029 (0.008-0.100) | 0.77 | 0.31 | $0.096 |
| oracle | test | standard | 100 | 0.61 | 1/61 = 0.016 (0.003-0.087) | 0.75 | 0.39 | $0.121 |
| oracle | test | strict | 100 | 0.53 | 0/53 = 0.000 (0.000-0.068) | 0.66 | 0.47 | $0.146 |

- **Right receipts accepted**: of the receipts E2 got fully right, the share the
  gate let through. The rest were right but went to review anyway: the cost of
  a cautious gate.
- **E3 cost / 1000**: what sending only the rejected receipts to E3 would cost,
  at E3's measured $0.311 per 1,000 on SROIE. Sending every receipt costs
  $0.311.

Before the gate, `needs_review` alone accepted 47 test receipts end to end, 3
of them wrong. All three splits together, end to end:

| | Accepted | Wrong among accepted |
|---|---|---|
| `needs_review` only | 538 / 1000 | 20 (3.7%) |
| standard gate | 527 / 1000 | 13 (2.5%) |
| strict gate | 479 / 1000 | 5 (1.0%) |

### What still gets through

The 2 wrong receipts the standard gate accepted on the test split, end to end:

- `test-0002`: "COCONUT JELLY (L)", 4,000 under a 24,000 tea, is a topping
  without an add-on word. The strict level rejects it.
- `test-0051`: the tax line "Pb1 10%" was read as "Ph1 10%", so it is not
  recognised as tax and becomes an item. The items then add up to the total
  by themselves, so every check passes. An OCR error that keeps the sums
  consistent cannot be caught by sums.

On oracle input the one wrong receipt is `test-0002` again.

### Why the rest are rejected

The most common reasons on the test split, end to end (a receipt can have
several): the total does not match the items (17), a free line (14), the items
do not add up to the subtotal (10), no total found (10), a total found only by
arithmetic (8). Some are cheap to accept: on train, 55 of the 61 receipts whose
*only* reason was "total found by arithmetic" were right. Accepting them would
lift the accepted share by about 7 points but bring the error among the
accepted from 1.9% to 2.9% on train, so the gate keeps rejecting them.

## What this means

- **About half of the receipts never need a model.** End to end on test, the
  standard gate accepts 46%. The local step costs nothing, so the model bill
  drops by the same share: $0.168 instead of $0.311 per 1,000 with E3.
- **The accepted receipts are not all right.** End to end, 2.5% of the
  accepted receipts over all splits were wrong (4.3% on the 46 test receipts,
  with a wide interval). The strict level cuts this to 1.0% but sends 5 more
  receipts in 100 to the model. Which level to use is a product choice: how
  much wrong data is acceptable against how much model use.
- **These rates hold for CORD.** Receipts from another country, till system or
  scanner can pass the gate more or less often and be wrong more or less
  often. Before relying on the gate for new documents, measure it on a labelled
  sample of them (a pilot set of 100 to 200), and keep sampling accepted
  documents at random afterwards.

## Not in this step

- **Routing to a model is not wired.** The API returns the decision (`gate` in
  the `/extract` response), and the UI shows how many documents were accepted
  without a look. Sending the rejected ones to E5 or E3 is the next step, and
  it needs those engines to read line items: today their prompts ask only for
  the four header fields, so a model could not replace the items the gate
  rejected.
- **Field notes are not measured.** In the product the gate also rejects a
  receipt with a field note; on CORD it sees the line items only, see above.

## Reproduce

```bash
python -m evaluation.cord_eval --mode e2e --split validation   # per-receipt results
python -m evaluation.cord_gate --mode e2e --split validation   # the gate on them
python -m evaluation.cord_gate --table                         # the table above
```

Summaries: `evaluation/results/cord/gate-<mode>-<split>.json`.
