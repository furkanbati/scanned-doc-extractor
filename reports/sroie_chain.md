# The engine chain on SROIE fields

The API can now run a mode as a chain (profiles.yaml `chain`): E2 reads the
receipt, and if its review gate rejects the result, the next engine reads it.
On CORD line items E2 → E3 handled 76 of 100 receipts without a person for
about half of E3's cost ([reports/cord_e3_line_items.md](cord_e3_line_items.md)).
This checks what the chain does to the four header fields, on the 400 SROIE
test receipts.

- E2 was run on every receipt with the API's checks
  ([evaluation/sroie_chain.py](../evaluation/sroie_chain.py)): 4.8 s per
  receipt, field accuracy 0.631 as in its report.
- E3's and E5's answers are those of their own SROIE runs (E3 with line items,
  2026-11-19; E5 on the Arc iGPU). Nothing was tuned on this split.

## Results

| Chain | Kept by E2 | Field accuracy | Second engine alone | E2 on what it kept | Second engine on those | s/receipt | USD per 1,000 |
|---|---|---|---|---|---|---|---|
| E2 → E3 | 21/400 | 0.791 | 0.801 | 0.643 | 0.821 | 15.9 | 0.49 |
| E2 → E5 | 21/400 | 0.814 | 0.825 | 0.643 | 0.857 | 24.6 | 0 |

- **The gate keeps only 21 of 400 receipts with E2** (5%; 46% on CORD). E2's
  line-item rules were written on Indonesian receipts; on these Malaysian ones
  the sums rarely add up ("expected total ... found ..." on 243 receipts,
  "N x price is not amount" on 191).
- **The receipts it does keep have the wrong company and address.** On those
  21, E2 gets every date and total right, but the company right on only 10 and
  the address on 2. The gate checks numbers; it cannot see a misread name.
- **So on these receipts the chain is worse than the second engine alone**:
  one point less accurate, barely cheaper (E3: $0.49 against $0.51 per 1,000)
  and slower, since E2 runs first on every receipt.

## What this means

- The chain pays off where E2's rules fit the receipts and the line items are
  what matters (CORD), not where the header fields matter and the rules do not
  fit (SROIE).
- A gate that only checks numbers lets wrong names through. Before a chain is
  offered for header fields, the gate needs a check on the company and
  address, for example agreement between two engines.
- No chained mode is offered in profiles.yaml for now; the API supports one,
  measured per document type before it is switched on.

## Reproduce

```bash
python -m evaluation.run --config e3            # and e5: the second engines' answers
python -m evaluation.sroie_chain --run          # E2 with its gate decisions, free
```
