# Roadmap: known gaps

Things that are missing or weak today, collected while building the CORD line
items, the review gate and E5's line items. Not in priority order within a
section.

## Line items

1. **E3 and E4 do not read line items.** Their prompts ask only for the four
   header fields. E5 shows how: ask for the items in the same call and run the
   answer through `parse_line_items` (extractor/fields/model_items.py).
2. **E4's LLM never sees the boxes.** It gets OCR lines joined into plain text.
   Rows with the amount column marked (from the line-item rules) might help it.
3. **Add-ons counted as items** are the most common silent error. A row
   classifier (gradient boosting on the features the rules already compute,
   trained on CORD train) could replace the keyword table in `classify_row`.
4. **A quantity printed after the name stays in the name** ("JAP CHAE 1").

## Checks and the hybrid chain

5. **Rejected documents are not sent on to E5.** The E2 -> E5 -> person chain
   exists only in the evaluation (`cord_gate --hybrid`), not in the API.
6. **No retry with feedback.** When a model's numbers do not add up, it is not
   asked again ("the items add up to X, you said Y").
7. **User corrections are not stored.** Edits in the UI only go into the
   downloaded file; they are needed for auditing and for new rules or models.
8. **No random audit or monitoring in production**: no sample of accepted
   documents checked by a person, no pass-rate or review-reason tracking.

## Fields and data

9. **Header-field rules are Malaysian.** On Indonesian receipts nearly every
   receipt gets "date not found", and the gate rejects it for that.
10. **Keywords live in code.** Move them to one YAML file per language.
11. **Not measured:** E3 on CORD line items. (E4 and E5 on SROIE fields: 0.744
    and 0.825, see evaluation/results/comparison.md.)
12. **Line items are measured on CORD only.** No pilot set of receipts from
    another country.
13. **Only the first page of a PDF is read.**

## Infrastructure

14. **E5 is still slow.** On the Intel Arc 130T iGPU it takes 21 s per receipt
    with line items (33 s on the CPU for the same work). Try smaller images, or
    reading the fields without the items when the items are not needed.
15. **Format fingerprinting and per-format rules** (steps 3 and 4 of the hybrid
    plan) are not started.
