# Digital documents: e-Fatura XML and text PDFs

Most invoices in Turkey are electronic. An e-Fatura (or e-Arşiv invoice) is an
XML file in UBL-TR, and the PDF a supplier sends is usually made by software,
with its text inside. Neither needs OCR.

## e-Fatura XML

`extractor/efatura.py` reads the invoice type's fields from their UBL tags:

| Field | UBL-TR |
|---|---|
| company | supplier's PartyName, else RegistrationName, else the person's name |
| tax_id | supplier's PartyIdentification with scheme VKN or TCKN |
| invoice_number | ID |
| date | IssueDate |
| subtotal | LegalMonetaryTotal/TaxExclusiveAmount |
| tax | TaxTotal/TaxAmount (the invoice's, not the lines') |
| total | LegalMonetaryTotal/PayableAmount |

Line items come from InvoiceLine (name, quantity, unit price, amount, discount).
ETTN, currency, scenario, invoice type and buyer go to `meta`, so the invoice
type and its measured fields stay as they are.

The invoice's amounts are checked against each other, allowing 0.01 for
rounding: the lines add up to their total; discounts and charges explain the
amount before tax; before tax plus tax is the amount with tax; the amount to
pay follows from it after withholding (tevkifat), prepayment and rounding. An
invoice that fails one is marked for a check, as is one missing a field.

## Text PDFs

`extractor/ocr/pdf_text.py` takes the first page's characters and their
boxes from the PDF (pypdfium2). Characters run into a piece until a line
break or a gap wider than 1.5 letter heights, as OCR gives a row's columns as
separate boxes; pieces on one row are joined into a line. Boxes are scaled to
the page rendered at 200 dpi, so they match the image the UI outlines.

A page with fewer than 20 letters and digits is treated as a scan, and a
rotated page too (its text coordinates do not follow the rendered image);
both go to OCR.

A scanner that adds its own OCR layer makes a PDF with text: that text is
used, and it is only as good as the scanner's OCR. `pdf_text=off` reads such
files with this project's OCR instead.

## Check on made-up invoices

Not a measurement: there is no labelled set of real e-Fatura files or text
PDFs. Four invoices of one made-up supplier, each written as UBL-TR XML and as
an HTML page printed to PDF by Edge (Turkish letters included):

- **XML:** all seven fields right on all four, accepted, 2-3 ms each, every
  line item read.
- **Text PDF:** a rule set taught from three of them through the API (lines
  picked by their labels, values left to learning) read the fourth right on
  all seven fields and accepted it. Reading the text layer took 1.4 ms.
- **The same with OCR** (`pdf_text=off`): six of seven right, 4.1 s. OCR read
  the company "ABC GIDA SANAYİ VE TİCARET A.Ş." as "...SANAYI VE TICARET A.$.",
  and the rule set learned it that way, so it was consistent but not the
  printed name.

Not checked yet: real e-Fatura files from several integrators, PDFs from
accounting software with unusual layouts (text drawn in pieces out of order),
and multi-page invoices (only page 1 is read).
