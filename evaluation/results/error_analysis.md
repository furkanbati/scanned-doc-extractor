# Error analysis

How the experiments fail, beyond the accuracy numbers in
[comparison.md](comparison.md). Test split, 400 receipts.

## 1. Part of the "errors" are the labels, not the model

The labels write the same address with and without a space after a comma
(`TAMPOI, 81200`, `TAMPOI,81200`, `TAMPOI 81200`). An exact match counts every
such difference as an error. Ignoring spacing and punctuation:

| Experiment | Mean field accuracy | Ignoring spacing |
|---|---|---|
| E1 Tesseract + rules | 0.464 | 0.498 |
| E2 RapidOCR + rules | 0.631 | 0.686 |
| E3 OpenAI gpt-6-luna | 0.797 | 0.858 |

For E3, 81 of its 206 address errors differ from the label only in spacing or
punctuation. The exact match stays the headline number; the second column shows
how much of the gap is formatting.

## 2. Totals and dates are solved; names and addresses are not

E3 gets the total wrong on 9 of 400 receipts and the date on 10. The
remaining errors are in the company name (99) and the address (206), the fields
with the most free text and the least consistent labels.

With rules (E1, E2) the date and total depend on the OCR: going from Tesseract
to RapidOCR cut date errors from 102 to 16. The company and address need the
rules to understand the layout, which is where the model (E3) gains most.

## 3. Close misses: mostly the model, sometimes the print

Errors of at most 2 letters or digits ("near" in comparison.md), checked
against the image for four receipts:

| Receipt | Printed | Label | E3 | Whose error |
|---|---|---|---|---|
| 046 | `JIN SENG`, clear | `JIN` | `JIM` | model |
| 057 | `UNIHAKKA`, clear; date in small print `2018` | `UNIHAKKA`, 2018 | `UNIJHAKKA`, 2015 | model, small print |
| 026 | stylised font, `TEO` or `TED` | `TED` | `TEO` | ambiguous print |
| 155 | `Gerbang Alaf`, first letters faded | `GERBANG` | `BERJAYA` | model, **invented a word** |

The UNIHAKKA receipts (one chain, many receipts) are misread differently each
time: `UNIJHAKKA`, `UNIBAKKA`, `UNIHANKA`.

Receipt 155 is the important one: where the print is faded, the model filled
in a plausible but wrong company name instead of leaving the gap. A
production setup should flag low-quality scans for a human check rather than
trust every value.

## 4. Label conventions the model does not know

- A branch name in brackets belongs to the company in some labels
  (`CHECKERS HYPERMARKET SDN BHD (JALAN KLANG LAMA)`, receipt 314) and to the
  address in others. The model put it in the address.
- A licensee line (`MCDONALD'S RAWANG DT(#188)`, receipt 155) was added to the
  address; the label leaves it out.

These are conventions of this dataset; a client's own data would define them
in the field descriptions (`extractor/doctypes/*.yaml`).

## 5. Known gap in the normalization

A date printed as `05 02-18` (receipt 070, a space and a dash) is not parsed,
so it cannot match `2018-02-05`. One receipt in the test split; not fixed, to
keep the measured numbers as they are.
