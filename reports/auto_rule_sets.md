# Rule sets without a user: chosen and learned automatically

A rule set reads one format in a few seconds without a model
([templates.md](templates.md)), but someone had to teach it and choose it.
For an agent or a batch nobody does either. Two `/extract` parameters do it
instead, both off by default:

- `template=auto`: the rule set is chosen by the document's fingerprint
  (`extractor/templates/choose.py`);
- `learning=auto`: rule sets are learned from a model's answers
  (`extractor/templates/auto.py`).

Everything here is measured on SROIE dev (226 receipts, the split for
tuning; the test split was not used) and checked on four made-up invoices.
Reproduce with `python -m evaluation.auto_eval` (README).

## Choosing the rule set

The document is read once (E2's OCR, or a PDF's text layer) and given to the
rule set whose fingerprint lines it prints the largest share of, at least the
60% the gate asks of a document read with a rule set. On a tie, the longer
fingerprint wins. The chosen rule set reads the same lines: OCR runs once.

On dev, rule sets taught per store as in templates.md (6 stores), every other
receipt given to the chooser (`evaluation/results/auto/choose-dev.json`):

| Receipts | Own rule set | Another's | None |
|---|---|---|---|
| of the 6 stores with a rule set | 52 | 0 | 0 |
| of other stores | — | 6 | 135 |

The 6 were receipts of other branches of one chain (MR D.I.Y.), and one of
an electrical shop, laid out like a store with a rule set. The rule set's
gate rejected all 6 ("company not found": the constant name is not printed),
so they went to E5 and none was misread.

## Grouping documents of one format

A document of no known format, once its teacher's answer is kept, joins the
waiting group whose first example it is most alike, if the likeness is at
least `GROUP_ALIKE`: the share of the shorter document's lines with letters
that are printed alike on both (`evaluation/results/auto/likeness-dev.json`):

| Pairs of receipts | Pairs | Likeness |
|---|---|---|
| one store | 578 | 0.231 and up |
| two stores | 3,793 | 0.208 at most |

| Threshold | One store's pairs split | Two stores' pairs joined |
|---|---|---|
| 0.25 | 2 | 0 |
| **0.3** | **6** | **0** |
| 0.5 | 85 | 0 |

0.3 is used: no two stores joined, few of one store split (a split only
delays learning).

## Learning from a teacher

The teacher is the chosen mode's model: E5 (private) or E3 (high accuracy).
E2's general rules never teach, since the gate cannot see a misread name
(sroie_chain.md). An answer becomes an example when every required field is
found and well formed and, where the type has a subtotal, a tax and a total,
they add up within 0.01. Three examples of a format teach its rule set,
learned and self-checked as a user's are. Then:

- **probation:** its first 5 accepted reads are read by the teacher too; one
  disagreement removes it, and the teacher's answer is returned;
- **audit:** afterwards 2% of its accepted reads come back marked `audit`.

### Which answers may teach

First planned: only answers the gate accepts (fields, and the line items
adding up). Measured on dev as a stream (receipts shuffled with a fixed seed,
E5's cached answers as the teacher), beside the checks used now:

| Teacher check | E5 answers kept | Rule sets learned | Reads by rule sets | of them after probation | Accuracy, all | E5 alone |
|---|---|---|---|---|---|---|
| gate (line items too) | 27 | 1 | 1 | 0 | 0.816 | 0.816 |
| **fields and sums (used)** | **225** | **11** | **24** | **2** | **0.816** | **0.816** |

The gate accepted 27 of 226 of E5's answers on receipts, and E5 reads no line
items on invoices, so with the gate nothing would be learned there at all.
With the checks used now, accuracy stayed E5's, no rule set accepted a receipt
of another store, and the rule sets read their 24 receipts as well as E5 did
(0.854 both). SROIE has few receipts per store, so 22 of those 24 were still
on probation (E5 read them too): E5 was spared on 2 receipts, 49 s. The gain
comes with a format that repeats many times (a supplier's monthly invoices),
which SROIE does not have.

### The teacher is the limit

- One rule set was removed on probation (receipt 373): it read the total
  46.06, as the label says; E5 read 46.82. Probation compares with the same
  teacher, so a right rule set can be removed when the teacher is wrong.
- Four made-up invoices of one layout, rendered as images, `learning=auto`
  through the API:

| Teacher | Answers kept | Why not | Rule set | Fourth invoice |
|---|---|---|---|---|
| E5 | 1 of 4 | subtotal plus tax is not the total (E5 took the first item as the subtotal) | not learned | read by E5 |
| E3 | 4 of 4 | — | learned at the third | read by the rule set, 7/7 right, 2.6 s, agreed on probation |

E5 also dropped a digit of every invoice number ("ABC202400000153" for
"ABC2024000000153"), which no check sees. A rule set learned from such
answers would read the document's own number and then disagree with E5 on
probation.

So the teacher should be E3 where documents may leave the machine for the
first few of each format; with E5, fewer rule sets are learned, and they are
only as right as E5. Ideas are on the roadmap (item 22): E3 for the first
documents of a format even in private mode, asked per format, or two teachers
that must agree; and corrections becoming examples (item 7).

## Not measured

- Many documents of one format (the case this is for): SROIE has few per
  store; no labelled set of a supplier's invoices.
- Real invoices: the invoice check is on four made-up ones.
- How often audits find errors: nothing records audit outcomes yet.
