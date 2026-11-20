"""Rule sets learned from a few documents of one format (extractor/templates)."""

import pytest

from extractor.ocr.base import OcrLine, rect_box
from extractor.schema import load_doctype
from extractor.templates import store
from extractor.templates.learn import Example, fingerprint, learn, learn_field, self_check
from extractor.templates.read import decide, disagreements, extract, fingerprint_found, read_field
from extractor.templates.values import ends_with, find_values, printed_in, same_value

DOCTYPE = load_doctype("receipt")


def lines(*texts):
    return [OcrLine(text=text, box=rect_box(0, 20 * i, 200, 20 * i + 15)) for i, text in enumerate(texts)]


def invoice(number, date, total, item="PEN", address=("NO 1, JALAN SAGU", "81200 JOHOR")):
    return lines(
        "KEDAI BUKU MAJU SDN BHD",
        *address,
        "TEL: 07-1234567",
        f"INVOICE NO {number}",
        f"DATE: {date}",
        f"{item} 1 x {total}",
        f"TOTAL RM {total}",
        "CASH 50.00",
        "THANK YOU PLEASE COME AGAIN",
    )


def example(number, date, total, **kwargs):
    address = kwargs.get("address", ("NO 1, JALAN SAGU", "81200 JOHOR"))
    return Example(invoice(number, date, total, **kwargs),
                   {"company": "KEDAI BUKU MAJU SDN BHD", "address": " ".join(address), "date": date, "total": total})


EXAMPLES = [example(101, "01/02/2019", "12.50"), example(102, "03/02/2019", "7.80", item="INK"),
            example(103, "15/03/2019", "21.00", item="BOOK")]


def test_three_documents_of_one_format_teach_a_rule_set():
    learned = learn("Kedai Buku", DOCTYPE, EXAMPLES)

    assert learned.unstable == {}
    rules = learned.template["fields"]
    assert rules["company"]["kind"] == "constant"
    assert (rules["date"]["kind"], rules["date"]["label"]) == ("label", ["DATE"])
    assert rules["total"]["kind"] == "label" and rules["total"]["label"][-1] == "RM"


def test_the_rule_set_reads_a_new_document_of_the_format():
    template = learn("Kedai Buku", DOCTYPE, EXAMPLES).template

    fields = extract(template, DOCTYPE, invoice(104, "20/04/2019", "9.90", item="RULER"))

    assert fields == {"company": "KEDAI BUKU MAJU SDN BHD", "address": "NO 1, JALAN SAGU 81200 JOHOR",
                      "date": "20/04/2019", "total": "9.90"}


def test_a_value_that_does_not_follow_one_layout_asks_for_another_example():
    moved = Example(lines("KEDAI BUKU MAJU SDN BHD", "NO 1, JALAN SAGU", "81200 JOHOR", "TEL: 07-1234567",
                          "THANK YOU 2.00", "PAID 9.00", "THANK YOU PLEASE COME AGAIN", "DATE: 05/05/2019"),
                    {"company": "KEDAI BUKU MAJU SDN BHD", "address": "NO 1, JALAN SAGU 81200 JOHOR",
                     "date": "05/05/2019", "total": "9.00"})

    learned = learn("Kedai Buku", DOCTYPE, EXAMPLES[:2] + [moved])

    assert learned.template is None
    assert "total" in learned.unstable


def test_fewer_than_three_examples_are_refused():
    with pytest.raises(ValueError):
        learn("Kedai Buku", DOCTYPE, EXAMPLES[:2])


def test_a_missing_value_on_an_example_is_reported():
    missing = Example(EXAMPLES[2].lines, {**EXAMPLES[2].values, "date": None})

    assert learn("Kedai Buku", DOCTYPE, EXAMPLES[:2] + [missing]).unstable == {"date": "a value is missing on an example"}


def test_a_field_that_differs_between_branches_is_not_a_constant():
    branches = [example(101, "01/02/2019", "12.50", address=("LOT 5, JALAN ABC", "50000 KL")),
                example(102, "03/02/2019", "7.80", address=("NO 9, JALAN XYZ", "10000 PENANG")),
                example(103, "15/03/2019", "21.00", address=("NO 1, JALAN SAGU", "81200 JOHOR"))]

    rule = learn("Kedai Buku", DOCTYPE, branches).template["fields"]["address"]

    # Not one address: the lines between the seller's name and TEL are.
    assert rule["kind"] == "label" and rule["where"] == "below"
    assert read_field(rule, "text", [l.text for l in invoice(1, "01/01/2019", "1.00", address=("A ST 1", "B CITY"))]) == "A ST 1 B CITY"


def test_a_date_without_a_label_is_read_by_its_place():
    texts = [["SHOP", "01/02/2019 10:00", "TOTAL 5.00"], ["SHOP", "03/02/2019 11:00", "TOTAL 6.00"]]

    rule = learn_field("date", texts, ["01/02/2019", "03/02/2019"])

    assert rule["kind"] in ("nth", "label")
    assert read_field(rule, "date", ["SHOP", "09/09/2019 12:00", "TOTAL 1.00"]) == "09/09/2019"


def test_the_self_check_names_the_example_a_rule_from_the_others_misreads():
    texts = [["TOTAL 5.00"], ["TOTAL 6.00"], ["AMOUNT 7.00"]]

    assert self_check("amount", texts, ["5.00", "6.00", "7.00"]) is not None
    assert self_check("amount", texts[:2] + [["TOTAL 7.00"]], ["5.00", "6.00", "7.00"]) is None


def test_a_label_may_be_misread_by_one_letter():
    rule = {"kind": "label", "where": "same", "label": ["TOTAL"], "occurrence": "first"}

    assert read_field(rule, "amount", ["T0TAL 4.20"]) is None  # "T" alone is no word
    assert read_field(rule, "amount", ["TOTAI 4.20"]) == "4.20"
    assert ends_with(["GRAND", "TOTAL"], ["TOTAL"])


def test_the_last_occurrence_of_a_label_can_be_the_one():
    rule = {"kind": "label", "where": "same", "label": ["TOTAL"], "occurrence": "last"}

    assert read_field(rule, "amount", ["TOTAL 1.00", "TOTAL 2.00"]) == "2.00"


def test_a_text_after_a_label_on_its_line():
    rule = {"kind": "label", "where": "same", "label": ["BILL", "TO"], "occurrence": "first"}

    assert read_field(rule, "text", ["BILL TO: ACME TRADING"]) == "ACME TRADING"


def test_text_below_a_label_ends_at_the_stop_word():
    rule = {"kind": "label", "where": "below", "label": ["BHD"], "occurrence": "first", "stop": ["TEL"]}

    assert read_field(rule, "text", ["ABC SDN BHD", "LINE 1", "LINE 2", "TEL 123"]) == "LINE 1 LINE 2"
    assert read_field(rule, "text", ["ABC SDN BHD", "LINE 1", "LINE 2"]) is None


def test_a_constant_must_be_printed_on_the_document():
    rule = {"kind": "constant", "value": "KEDAI BUKU MAJU SDN BHD", "lines": 1}

    assert read_field(rule, "text", ["KEDAI BUKU MAJU SDN BH0", "TOTAL 1.00"]) == "KEDAI BUKU MAJU SDN BHD"
    assert read_field(rule, "text", ["ANOTHER SHOP", "TOTAL 1.00"]) is None


def test_values_are_compared_as_their_type():
    assert same_value("date", "01/02/2019", "2019-02-01")
    assert same_value("amount", "RM 12.5", "12.50")
    assert same_value("text", "KEDAI BUKU MAJU SDN BH0", "KEDAI BUKU MAJU SDN BHD")
    assert not same_value("text", "ANOTHER SHOP", "KEDAI BUKU MAJU SDN BHD")
    assert [found.value for found in find_values("DATE 01/02/2019 TOTAL 1,234.50", "amount")][-1] == "1234.50"


def test_the_fingerprint_keeps_lines_every_example_prints():
    fixed = fingerprint([example.lines for example in EXAMPLES])

    assert "THANK YOU PLEASE COME AGAIN" in fixed
    assert "KEDAI BUKU MAJU SDN BHD" in fixed
    # Item lines differ between the examples.
    assert all("PEN" not in text for text in fixed)


def test_the_gate_accepts_a_document_of_the_format():
    template = learn("Kedai Buku", DOCTYPE, EXAMPLES).template

    gate = decide(template, DOCTYPE, invoice(104, "20/04/2019", "9.90"), {})

    assert gate.accepted
    assert fingerprint_found(template, invoice(104, "20/04/2019", "9.90"))[0] == len(template["fingerprint"])


def test_the_gate_rejects_another_format():
    template = learn("Kedai Buku", DOCTYPE, EXAMPLES).template
    other = lines("PASAR MINI", "LOT 7", "RESIT", "MILK 3.00", "JUMLAH 3.00")

    gate = decide(template, DOCTYPE, other, {"date": "not found"})

    assert not gate.accepted
    assert gate.reasons[0].startswith("rule set: only 0 of its")
    assert "date not found" in gate.reasons


def test_rule_sets_are_saved_under_the_users_name(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPLATES_DIR", str(tmp_path))
    template = learn("ABC Tedarik Ş.A.", DOCTYPE, EXAMPLES).template

    saved = store.save(template)

    assert saved.name == "abc-tedarik-s-a.yaml"
    assert store.load("ABC Tedarik Ş.A.")["fields"] == template["fields"]
    assert store.names()["abc-tedarik-s-a"]["examples"] == 3
    with pytest.raises(FileExistsError):
        store.save(template)
    store.delete("abc-tedarik-s-a")
    assert store.names() == {}
    with pytest.raises(KeyError):
        store.load("abc-tedarik-s-a")


def test_a_name_without_letters_or_digits_is_refused():
    with pytest.raises(ValueError):
        store.slug("--- !")


def test_labels_that_agreed_on_the_examples_must_agree_on_a_new_document():
    # Every example was paid exactly, so CASH read the total too.
    texts = [["ITEM 5.00", "TOTAL 5.00", "CASH 5.00"], ["ITEM 6.00", "TOTAL 6.00", "CASH 6.00"],
             ["ITEM 7.00", "TOTAL 7.00", "CASH 7.00"]]
    rule = learn_field("amount", texts, ["5.00", "6.00", "7.00"])
    template = {"fields": {"total": rule}, "fingerprint": []}

    assert any(other["label"] == ["CASH"] for other in [rule, *rule["also"]])
    assert disagreements(template, DOCTYPE, lines("ITEM 8.00", "TOTAL 8.00", "CASH 10.00"))[0].startswith("total read as")
    assert disagreements(template, DOCTYPE, lines("ITEM 8.00", "TOTAL 8.00", "CASH 8.00")) == []


def test_a_value_found_by_the_short_label_only_is_doubted():
    rule = {"kind": "label", "where": "same", "label": ["RM"], "occurrence": "first",
            "also": [{"kind": "label", "where": "same", "label": ["TOTAL", "RM"], "occurrence": "first"}]}
    template = {"fields": {"total": rule}, "fingerprint": []}

    assert disagreements(template, DOCTYPE, lines("PEN RM 2.30", "PAID 9.00")) == ["total found by one label only"]
    assert disagreements(template, DOCTYPE, lines("TOTAL RM 9.00")) == []


def test_the_example_that_does_not_fit_the_others_is_named():
    rounded = Example(lines("KEDAI BUKU MAJU SDN BHD", "NO 1, JALAN SAGU", "81200 JOHOR", "TEL: 07-1234567",
                            "INVOICE NO 104", "DATE: 20/04/2019", "PEN 1 x 9.98", "TOTAL RM 9.98",
                            "CASH 50.00", "THANK YOU PLEASE COME AGAIN"),
                      {"company": "KEDAI BUKU MAJU SDN BHD", "address": "NO 1, JALAN SAGU 81200 JOHOR",
                       "date": "20/04/2019", "total": "10.00"})

    learned = learn("Kedai Buku", DOCTYPE, [EXAMPLES[0], rounded, EXAMPLES[1], EXAMPLES[2]])

    assert learned.template is None
    assert learned.misfits == {"total": 1}


def test_a_constant_may_be_printed_with_more_on_its_line():
    assert printed_in("99 SPEED MART S/B", "99 SPEED MART S/B (519537-X)") >= 90
    assert printed_in("99 SPEED MART S/B", "99 SPEED MART S/B LOT P.T. 2811, JALAN ANGSA, TAMAN BERKELEY") == 0
