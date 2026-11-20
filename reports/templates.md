# Rule sets taught from three documents

A user who receives the same format again and again (one supplier's invoices)
can teach a rule set from three examples: E2 fills in the values, the user
corrects them, and code learns where each value is printed
([extractor/templates](../extractor/templates)). No model is involved, and
nothing leaves the machine. Documents are then read with the rule set on E2's
OCR; the ones its gate rejects go to E5, the local model, with a warning.

## How it was measured

SROIE has many receipts from the same store, the closest stand-in for "one
format". For each store with at least five receipts
([evaluation/templates_eval.py](../evaluation/templates_eval.py)):

- the store's first receipts by id are the examples, their labels standing in
  for the values a user confirms. When the self-check names an example that
  does not fit the others, it is replaced with the next receipt; otherwise one
  more is added; at most six are shown;
- every other receipt of the store is read with the rule set;
- every receipt of the other stores is read with it too, to check that the
  rule set does not accept a format it was not taught.

The rules and thresholds were tuned on the dev split (8 stores, 94 receipts).
The test split (15 stores, 213 receipts) was run once, at the end. E5's and
E2's answers on the same receipts are those of their own test runs.

## Results on the test split

- **Rule sets learned:** 13 of 15 stores. Ten of them were learned from the
  first three receipts; three needed a fourth. 99 Speedmart and Popular Book
  were not learned after six receipts: their address and total are not printed
  in one fixed place.
- **Receipts read:** 145, the rest of those 13 stores' receipts.
- **Accepted by the rule set's gate:** 123 of 145 (85%).
- **Accepted receipts of another store:** none, in 2,582 tries.

| On those 145 receipts | Field accuracy | Company | Address | Date | Total | s/receipt |
|---|---|---|---|---|---|---|
| Rule set, accepted receipts only (123) | **0.959** | 1.000 | 0.886 | 0.992 | 0.959 | |
| Rule set, then E5 for the 22 rejected | **0.935** | 0.979 | 0.855 | 0.993 | 0.910 | 5.5 |
| E5 alone | 0.879 | 0.972 | 0.648 | 1.000 | 0.897 | 21.0 |
| E2 alone | 0.693 | 0.476 | 0.462 | 0.986 | 0.848 | 4.8 |

- **Rule set plus E5 is more accurate than E5 alone and four times faster.**
  The rule set returns the company and address exactly as the user confirmed
  them, where E5 rewrites the address its own way (0.65). It is free, like
  E5.
- **Most of the remaining errors are not the rule set's:**
  - SROIE labels the same address differently from one receipt to the next
    ("PARINDUSTRIAN" and "PERINDUSTRIAN" for the same store);
  - receipts with a rounding line show the total before rounding when the
    examples had none;
  - in one case a short label was learned that happened to fit the examples.

## What the gate checks

A document is accepted only when all of these hold:

- it prints at least 60% of the lines all the examples printed alike (its
  fingerprint);
- every field is found and well formed;
- the other labels that read every example right agree with the chosen one on
  this document.

That last check exists because three examples cannot tell the right label
from one that only happened to agree. "CASH" read the total on every example
paid in exact change; on a document paid with a larger note, the two labels
disagree and E5 reads it.

Two more checks were tried on dev and left out:

- comparing the total with E2's line items rejected more right results than
  wrong ones;
- a stricter fingerprint threshold (0.7 to 0.8) changed little.

## Limits

- Only the four receipt fields. Custom fields (invoice number, tax number) and
  line-item columns are not learned yet; line items come from E2's general
  rules and keep their own check.
- The user picks the rule set; documents are not matched to one automatically.
- A layout the examples never showed (a rounding line, a second page) is not
  learned. The self-check catches it among the examples, not in documents
  that come later. Teaching with varied examples helps.
- Measured on Malaysian receipts, not on invoices. The method does not depend
  on the language: labels are learned from the examples, not from a word list.

## Reproduce

```bash
python -m evaluation.templates_eval --split dev --ocr    # E2's OCR once per receipt, cached in data/
python -m evaluation.templates_eval --split dev
python -m evaluation.templates_eval --split test --ocr   # once, at the end
```
