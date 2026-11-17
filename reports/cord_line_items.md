# Line items on CORD: E2 rules (RapidOCR + rules, no model)

E2 extracts every purchased item with its amount, the subtotal, tax, service,
discount and total from a receipt, and sets `needs_review` when the numbers do
not add up. This report measures it on CORD v2, in two modes, and explains
where it fails. No language model or paid API is involved; after the dataset
is downloaded everything runs offline.

**Test split: run once**, on 2026-11-16, after the rules were frozen at commit
`b3db1d0`. Every rule and threshold was developed on train and validation
only; no rule was changed after the test run.

## Data

[CORD v2](https://huggingface.co/datasets/naver-clova-ix/cord-v2) (Park et al.,
*CORD: A Consolidated Receipt Dataset for Post-OCR Parsing*, Document
Intelligence Workshop at NeurIPS 2019), CC-BY-4.0: 1,000 Indonesian receipts,
split by its authors into train 800 / validation 100 / test 100. The labels
used here (`gt_parse`):

- **items**: the top-level `menu` entries, in printed order, each with its line
  amount (`price`). Modifiers (`menu.sub`, "+Hot", "Extra Pearl") are not items.
- **total**: `total.total_price`; 21 train, 2 validation and 5 test receipts
  have none and are scored on their items only.
- subtotal, tax, service and discount are loaded too, but are not required values.

A value printed twice (`["55,834", "55,800"]`) keeps the last, lowest one. All
amounts, labels and predictions alike, go through one `parse_amount()`
(`extractor/amounts.py`): "25,000", "25.000", "1,591,600", "9.999,99",
"Rp. 41.580" and "1,400 -" read as 25000, 25000, 1591600, 9999.99, 41580 and -1400.

## Modes

| Mode | Input to the rules | What it measures |
|---|---|---|
| **A, end to end** (`e2e`) | image -> RapidOCR (PP-OCRv6 small, CPU) segments with boxes | the realistic pipeline |
| **B, oracle OCR** (`oracle`) | CORD's own words and quads, without their field labels or row ids | the rules alone ("OCR is done, fix the parser") |

The oracle input is easier than real OCR in one more way than having no OCR
errors: CORD annotates only the item and total words, so the store name,
address, phone, footer and promotional text are not in it. The oracle score is
an upper bound for these rules, not what a perfect OCR engine would give.

## Metrics

Per receipt the **required values** are: the item count (1), each labelled
item's amount (matched in order, position by position) and the total (1).

- **Required values**: share of required values that are right, over all receipts.
- **Receipts fully right**: every required value right.
- **needs_review**: share of receipts flagged.
- **Silent errors**: receipts with at least one wrong required value that were
  *not* flagged. This is the number that has to stay near zero: a flagged
  receipt costs a person a look, a silent one is wrong data.
- **Amount P / R / F1**: item amounts matched regardless of order (multiset).
- **s/receipt**: OCR plus rules, Intel Core Ultra 5 226V, CPU only. The rules
  alone take about 1 ms.

## Results

| Mode | Split | Receipts | Item count | Item amounts | Total | Required values | Receipts fully right | needs_review | Silent errors | Amount F1 | s/receipt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| B oracle | train | 800 | 0.868 | 0.930 | 0.965 | 0.924 | 0.841 | 0.344 | 0.014 | 0.955 | 0.00 |
| B oracle | validation | 100 | 0.890 | 0.959 | 1.000 | 0.952 | 0.870 | 0.230 | 0.040 | 0.957 | 0.00 |
| B oracle | **test** | 100 | 0.830 | 0.880 | 0.968 | **0.888** | 0.800 | 0.370 | **0.030** | 0.939 | 0.00 |
| A e2e | train | 800 | 0.804 | 0.844 | 0.905 | 0.848 | 0.714 | 0.464 | 0.016 | 0.900 | 1.62 |
| A e2e | validation | 100 | 0.870 | 0.905 | 0.939 | 0.905 | 0.760 | 0.380 | 0.040 | 0.908 | 1.58 |
| A e2e | **test** | 100 | 0.690 | 0.757 | 0.874 | **0.767** | 0.620 | 0.530 | **0.030** | 0.861 | 1.69 |

Amount precision / recall on test: oracle 0.912 / 0.967, end to end 0.841 / 0.882.

**Test is lower than validation**, by 6 points in the oracle mode and 14 end
to end. Validation (100 receipts) turned out easier than train, and test
harder: the rules were not overfitted to validation (train, 800 receipts,
sits between the two in both modes), but 100 receipts give a wide interval.
With one required value per item plus two per receipt, a few receipts with
long, modifier-heavy item lists move the score by several points. The train
numbers are the steadier estimate of how the rules behave; the test numbers
are the honest held-out ones.

**The silent-error rate held at 3% on test** in both modes, while needs_review
rose to 37% (oracle) and 53% (end to end): on harder receipts the
reconciliation flagged more instead of letting errors through. Five of the six
silent errors on test are one kind, a priced modifier counted as an item
(example 3 below). The sixth (test-0051, end to end) is a tax line whose
keyword OCR misread ("PB1" as "Ph1"): it was counted as an item, and with no
subtotal printed, items plus that "item" still equalled the total.

## What goes wrong

Error kinds per receipt with at least one wrong required value (a receipt can
have several kinds):

- **missed_row**: fewer items than labelled.
- **extra_row**: more items than labelled, the extra one not a sum (mostly priced modifiers).
- **subtotal_as_item**: an extra item whose amount is a sum (subtotal, total, or the items above it).
- **wrong_row**: right amounts, attached to the wrong rows.
- **number**: an amount with the right digits at the wrong scale, or one digit off.
- **total**: the total is wrong.
- **ocr** (end to end only): a labelled amount does not appear anywhere in the OCR text, so no rule could have found it.

| Kind | B oracle, validation | A e2e, validation | B oracle, train | A e2e, train |
|---|---|---|---|---|
| Receipts with an error | 13 | 24 | 127 | 229 |
| missed_row | 4 | 4 | 41 | 60 |
| extra_row | 5 | 4 | 51 | 53 |
| subtotal_as_item | 2 | 5 | 14 | 44 |
| wrong_row | 0 | 1 | 0 | 1 |
| number | 0 | 3 | 3 | 21 |
| total | 0 | 6 | 27 | 74 |
| ocr | - | 9 | - | 65 |

Read by mode:

1. **Rules alone (oracle): the item/modifier boundary.** Extra and missed rows
   are most of the oracle errors, and most of those are not misread rows: the
   labels themselves are inconsistent. In train, a free line ("Plastic Bag 0")
   is labelled a menu item 90 times and a modifier 86 times; "Extra Jelly Lychee 5,000"
   is an item on one receipt and a modifier on the next. Rules use what is
   general (a "+" prefix, indentation, a missing quantity among quantity lines)
   and flag every free or folded line, but the remaining cases cannot be told
   apart from layout or arithmetic.
2. **End to end: OCR.** About 30% of the end-to-end receipts with an error
   (65 of 229 on train, 9 of 24 on validation) have a labelled amount that the
   OCR text does not contain at all: a lost "0", "121,798" read as "121, 768",
   "TOTAL 000.800". Totals suffer most (0.905 end to end against 0.965 oracle
   on train), because a misread TOTAL keyword ("TTTAL", "GR TTAL") also hides
   the line's role.
3. **Sums taken as items** triple end to end (14 -> 44 on train): when OCR
   garbles the TOTAL or SUBTOTAL keyword, the line looks like one more item.
   These are flagged, since the items then no longer add up.

### Examples (validation)

1. **Reconciliation catches OCR digit errors** (validation-0081, end to end).
   OCR read 19,091 as 19,001 and 60,909 as 60,609, and the last item came out
   as 16,066 instead of 99,091. The items
   add up to 167,494 against a printed subtotal of 250,909, so the receipt is
   flagged; nothing wrong reaches the output unflagged.
2. **A misread total that looked like rounding** (validation-0070, end to end).
   Subtotal 103,000 + service 7,725 + tax 11,073 = 121,798; OCR read the total
   as "121, 768". A plain ±0.2% rounding tolerance accepted it, a silent error.
   The rule now accepts a near-miss only when the printed total is *rounder*
   than the sum (55,834 -> 55,800), as real rounding is; a misread digit is
   flagged.
3. **A modifier counted as an item** (validation-0071, oracle; same on test-0002,
   test-0007, test-0078). "Dum Dum Thai Coffee 19,000" followed by "Ice 1,000",
   subtotal 20,000. The label puts "Ice" under the coffee as a sub-item; it is
   not indented and has no "+", and the sums are right either way. This is the
   most common silent error left on test.
4. **An item that costs exactly what the items above add up to** (train-0294,
   oracle; fixed). Dumpling 9,000 + Jamur 8,000 + Caisim 7,000 = 24,000, and
   the next item, "2 Lapchiong", costs 24,000. The first version took it for a
   subtotal and lost three items. An unlabelled sum now ends the items only
   when summary lines (not more items) follow it.
5. **A pre-tax TOTAL line** (train-0066, end to end; fixed). "TOTAL 52,636",
   "TAX 10% 5,264", then "GR TTAL 57,900" with the keyword misread. The first
   TOTAL line added up with the items, so 52,636 was reported without a flag.
   Now tax printed below a TOTAL line, and a later line printing their sum,
   make the first one a subtotal (57,900 is reported, flagged as found by
   arithmetic).

## How the rules work

`extractor/fields/line_items.py`; one test per rule in `tests/test_line_items.py`,
all on made-up receipts.

- **Rows**: the page skew is the median slope of wide boxes; boxes are rotated
  back, OCR segments are split into words with interpolated positions, and a
  word joins the row whose horizontally nearest word overlaps it vertically.
  Numbers OCR split at a thousands gap ("20 000") are joined.
- **Amount column**: the right edges of each non-summary row's rightmost
  amount, clustered with a tolerance of 1.5 line heights; the largest cluster.
  No heading is read.
- **Row kinds**: one keyword table (`ROW_KEYWORDS`: TOTAL, SUBTOTAL, TAX, PB1,
  PPN, SERVICE, DISC, CASH, TUNAI, CHANGE, KEMBALI, ...) plus a percentage sign
  for unlabelled tax lines. The first summary line ends the items; so does an
  unlabelled line equal to the sum of the items above it, when summary lines
  follow.
- **Items**: one per amount in the column. A name line above an amount line
  without a name is that item's name ("AYAM BAKAR" / "2 x 15,000 30,000"),
  otherwise it continues the item above. Discounts and negative amounts reduce
  the item above. "+", indented, or (among quantity lines) quantity-less priced
  lines are modifiers of the item above. A "0 x1" line whose amount was not read
  becomes an item with amount quantity x unit price.
- **Totals**: each TOTAL line is checked against the subtotal and adjustments
  printed above it; the one equal to cash - change wins, then the last one that
  adds up. Tax and service below the total, before payment, turn it into a
  subtotal when a later line prints the sum. Without a usable TOTAL line, a
  printed line equal to the computed sum is taken.
- **needs_review** is set when: items do not add up to the subtotal; subtotal +
  tax + service + rounding - discount is not the total (a rounded total is
  accepted when it is rounder than the sum and within 0.2%); there is no total
  or no item; a TOTAL line was not found or a later line contradicts it; a
  free line, a folded modifier, a computed amount, a quantity x unit price that
  does not match, or a name without its amount below the last item is present.

## Limitations

- **needs_review is high**: 37% of test receipts in the oracle mode, 53% end
  to end. In the oracle mode about half of the flags on fully correct receipts
  (10 of 20 on test, 67 of 159 on train) come from free lines and folded
  modifiers, which are ambiguous in the labels rather than in the receipts; a
  client who counts modifiers one way consistently would see fewer flags. End
  to end most such flags come from totals and sums that OCR misread.
- Only the item count, item amounts and total are scored; item names,
  quantities, unit prices, tax and service are extracted but not measured.
- One country and language (Indonesia), printed receipts, one page.
- The line-item rules are not yet called by the API or the UI; they run
  through `extract_line_items(segments)` on RapidOCR segments read without
  line grouping (`RapidOcr(group_lines=False)`).

## Next improvement

Modifiers are most of the silent errors left and most of the oracle errors, so the
next rule should decide item versus modifier from what the receipt prints
about itself rather than from the line alone: a modifier's amount is usually
small next to its parent's, it often repeats under several items ("PEARL"
three times), and on receipts with a quantity column modifiers never carry
one. Scoring those signals together, and only then counting a line, would
attack extra_row and missed_row at once. End to end, the gap to the oracle is
mostly OCR (lost digits and keywords): running RapidOCR a second time on the
crop of a summary line that does not add up would recover some totals without
a model.

## Reproduce

```bash
bash scripts/download_cord.sh                               # 2.3 GB into data/cord/
python -m evaluation.cord_eval --mode oracle --split test   # Mode B
python -m evaluation.cord_eval --mode e2e --split test      # Mode A (RapidOCR, ~3 min)
python -m evaluation.cord_eval --table                      # the results table above
```

Each run writes its summary to `evaluation/results/cord/<mode>-<split>.json`
(committed) and per-receipt predictions next to it as `.receipts.jsonl` (not
committed). The end-to-end mode caches RapidOCR output in
`data/cord/ocr/<split>.jsonl`; the cached OCR time still counts in s/receipt.
