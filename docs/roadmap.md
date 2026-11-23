# Roadmap: known gaps

Things that are missing or weak today, collected while building the CORD line
items, the review gate and E5's line items. Not in priority order within a
section.

## Line items

1. **E4 does not read line items.** Its prompt asks only for the four
   header fields. E5 and E3 show how: ask for the items in the same call and run the
   answer through `parse_line_items` (extractor/fields/model_items.py).
2. **E4's LLM never sees the boxes.** It gets OCR lines joined into plain text.
   Rows with the amount column marked (from the line-item rules) might help it.
3. **Add-ons counted as items** are the most common silent error. A row
   classifier (gradient boosting on the features the rules already compute,
   trained on CORD train) could replace the keyword table in `classify_row`.
4. **A quantity printed after the name stays in the name** ("JAP CHAE 1").

## Checks and the hybrid chain

5. **No chained mode is offered; the gate needs a company/address check.**
   The API runs chains (profiles.yaml `chain`), but on SROIE the gate keeps
   only 21 of 400 receipts with E2, and on those the company is right on 10
   and the address on 2 (reports/sroie_chain.md): the gate checks numbers and
   cannot see a misread name. Future work, before a chain is offered for
   header fields: a check built on general signals, such as OCR confidence on
   the name lines, an address with a number or postcode, the name's position
   near the top. Tune it on train + validation with E2 only (no paid calls),
   then measure once on test. Worth it where E2's rules fit the documents
   (CORD-like); on SROIE it gains little, since E2 keeps so few.
6. **No retry with feedback.** When a model's numbers do not add up, it is not
   asked again ("the items add up to X, you said Y").
7. **User corrections are not stored.** Edits in the UI only go into the
   downloaded file. With learning=auto a correction should become an example
   of its format, and a correction to an audited read should send its rule set
   back to probation; the teacher's own errors (reports/auto_rule_sets.md)
   are only caught this way.
8. **Monitoring in production is thin.** Automatic rule sets mark 2% of what
   they accept for a person (`audit`), but nothing tracks pass rates, review
   reasons or audit outcomes over time.

## Fields and data

9. **The general header-field rules are Malaysian.** On Indonesian receipts
   nearly every receipt gets "date not found", and the gate rejects it for
   that. In the UI they now run only in the private mode until its local model
   is set up, and behind the scenes (picking a receipt's first example when a
   rule set is taught); E2 reads with rule sets learned from the user's
   examples, which take labels in any language.
10. **The general rules' keywords live in code.** Move them to one YAML file
    per language.
11. **E3 is measured with the line-item prompt** (SROIE fields 0.801, CORD
    line items in reports/cord_e3_line_items.md); its fields-only numbers stay
    as a comment in e3.yaml. Not measured: E4 with line items (item 1).
12. **Line items are measured on CORD only.** No pilot set of receipts from
    another country.
13. **Only the first page of a PDF is read.** A PDF holding several invoices
    should give one document per page (or let the user say which pages go
    together).

## Infrastructure

14. **E5 is still slow.** On the Intel Arc 130T iGPU it takes 21 s per receipt
    with line items (33 s on the CPU for the same work). Try smaller images, or
    reading the fields without the items when the items are not needed.
15. **Rule sets: line items are left** (reports/templates.md,
    reports/auto_rule_sets.md). Document types with the user's own fields,
    choosing the rule set by fingerprint (template=auto) and learning rule
    sets from a model's answers (learning=auto) are done. Next: line-item
    columns learned from the examples. The two SROIE stores it could not learn
    print their address and total in more than one place.
22. **Learning without a user is only as good as its teacher.** E5 misreads
    invoice subtotals and drops digits of long numbers; the sum check catches
    the first, not the second, and probation compares with the same teacher
    (on SROIE dev it removed a rule set that was right). Ideas: E3 as the
    teacher for the first documents of a format even in private mode (asked
    per format), or two teachers that must agree. Not measured on many
    documents of one format: SROIE has few per store.

## Digital documents

18. **Other digital forms are not read.** The e-Fatura's HTML view, an XML
    embedded in a PDF, and UBL documents other than invoices (dispatch notes,
    e-İrsaliye) go to OCR or are refused. The e-Fatura reader is checked on
    made-up XML only (reports/digital_documents.md), not yet on real
    invoices from several providers.

## Batch use

The Extract page already takes many files at once, reads them one after
another with a progress bar and gives one table to download.

16. **No ZIP upload.** Picking hundreds of files one by one is slow; a ZIP
    of images and PDFs should be unpacked and read like separate uploads.
17. **Large batches need the page open.** Reading is sequential inside the
    UI session. For thousands of documents: a command-line tool that reads a
    folder (with an engine or a rule set) and writes the Excel/CSV, resuming
    where it stopped.

## Installation and deployment

Today `docker compose up` runs the UI, the API (OCR, rules, XML and PDF
reading in one process) and Ollama on one machine, with ports open to that
machine only. An agent (n8n, LangGraph) would join the same compose network
and call `http://api:8000/extract`.

19. **The API has no authentication.** Fine while it listens on localhost
    only; before other computers or an agent on another machine use it, it
    needs a token or a reverse proxy (Caddy, Nginx) with TLS.
20. **Images are built from source on each machine.** A customer needs
    prebuilt images from a registry and a one-command install (and update).
21. **No guidance on where to run it.** A Windows PC with Docker Desktop is
    enough for a small office; an always-on Linux mini server is sturdier; E5
    wants a GPU (25 to 35 s per receipt on a CPU). Intel and AMD GPUs need
    Ollama on the host, outside Docker. Document the choices and the minimum
    hardware.
