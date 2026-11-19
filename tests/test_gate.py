import json
from decimal import Decimal

import pytest

from evaluation.cord_gate import gate_summary, results_table, run, wilson
from extractor.gate import add_on_items, cheap_items, decide


def item(name, amount):
    return {"name": name, "amount": amount, "quantity": None, "unit_price": None}


def line_items(items, reasons=()):
    return {
        "items": items,
        "subtotal": None,
        "tax": None,
        "service": None,
        "discount": None,
        "total": str(sum(Decimal(entry["amount"]) for entry in items)),
        "needs_review": bool(reasons),
        "reasons": list(reasons),
    }


GOOD = line_items([item("NASI GORENG", "25000"), item("ES TEH", "8000")])


def test_a_result_that_adds_up_is_accepted():
    decision = decide(GOOD, review={})

    assert decision.accepted
    assert decision.to_json() == {"accepted": True, "reasons": []}


def test_line_item_reasons_send_it_on():
    decision = decide(line_items([item("CAKE", "9000")], ["no total"]))

    assert not decision.accepted
    assert decision.reasons == ["line items: no total"]


def test_field_notes_send_it_on():
    decision = decide(GOOD, review={"date": "not found"})

    assert decision.reasons == ["date not found"]


def test_no_items_or_no_line_items_send_it_on():
    assert decide(line_items([])).reasons == ["line items: no items"]
    assert decide(None).reasons == ["line items: not read"]


def test_needs_review_without_a_reason_still_sends_it_on():
    flagged = {**GOOD, "needs_review": True}

    assert decide(flagged).reasons == ["line items: check"]


@pytest.mark.parametrize("name", ["Extra Jelly Lychee", "ADD CHICKEN BOX", "+ Ice", "Tambah Keju", "** topping pearl"])
def test_add_on_names(name):
    assert add_on_items([item("LEMON TEA", "25000"), item(name, "5000")]) == [name]


@pytest.mark.parametrize("name", ["ADDITIONAL RICE SET", "EXTRAVAGANZA PIZZA", "ICED TEA", "Paddle Pop"])
def test_names_that_only_look_like_add_ons(name):
    assert add_on_items([item("LEMON TEA", "25000"), item(name, "5000")]) == []


def test_the_first_item_is_never_an_add_on():
    assert add_on_items([item("Extra Large Pizza", "90000")]) == []


def test_an_add_on_item_sends_the_receipt_on_even_when_the_sums_agree():
    receipt = line_items([item("Lemon Tea (L)", "25000"), item("Extra Jelly Lychee", "5000")])

    decision = decide(receipt)

    assert not decision.accepted
    assert decision.reasons == ["line items: 'Extra Jelly Lychee' may be an add-on to the item above"]


def test_cheap_items_compare_with_the_item_above():
    items = [item("COFFEE", "28000"), item("PEARL", "4000"), item("TEA", "20000"), item("CAKE", "6000")]

    assert cheap_items(items) == ["PEARL"]
    assert cheap_items(items, share=Decimal("0.5")) == ["PEARL", "CAKE"]


def test_cheap_items_ignore_free_and_negative_lines():
    assert cheap_items([item("COFFEE", "28000"), item("SAUCE", "0"), item("DISC", "-2000")]) == []


def test_only_the_strict_gate_checks_cheap_items():
    receipt = line_items([item("COFFEE", "28000"), item("PEARL", "4000")])

    assert decide(receipt).accepted
    assert decide(receipt, strict=True).reasons == ["line items: 'PEARL' costs under a quarter of the item above"]


def test_wilson_interval():
    low, high = wilson(3, 47)

    assert 0.02 < low < 0.0639 < high < 0.18
    assert wilson(0, 0) == (0.0, 1.0)
    assert wilson(0, 100)[0] == 0.0


def record(prediction, right):
    return {"prediction": prediction, "correct": 3 if right else 2, "required": 3}


RECORDS = [
    record(GOOD, True),
    record(GOOD, False),  # passes every check but is wrong: a silent error
    record(line_items([item("CAKE", "9000")], ["no total"]), True),
    record(line_items([item("TEA", "9000"), item("Extra Pearl", "3000")]), False),
]


def test_gate_summary_counts_what_gets_through():
    summary = gate_summary(RECORDS, strict=False)

    assert summary["accepted"] == 2
    assert summary["accepted_wrong"] == 1
    assert summary["accepted_error_rate"] == 0.5
    assert summary["right_accepted_share"] == 0.5
    assert summary["routed_rate"] == 0.5
    assert summary["reasons"] == {"line items: no total": 1, "line items: '...' may be an add-on to the item above": 1}


def test_run_writes_the_summary_and_the_table(tmp_path):
    with (tmp_path / "e2e-validation.receipts.jsonl").open("w", encoding="utf-8") as file:
        for entry in RECORDS:
            file.write(json.dumps(entry) + "\n")

    summary = run("e2e", "validation", tmp_path)

    assert summary["standard"]["accepted"] == 2
    assert json.loads((tmp_path / "gate-e2e-validation.json").read_text(encoding="utf-8")) == summary
    table = results_table(tmp_path)
    assert "| e2e | validation | standard | 4 | 0.50 | 1/2 = 0.500" in table


def test_run_without_receipts_says_what_to_run(tmp_path):
    with pytest.raises(SystemExit, match="cord_eval"):
        run("e2e", "validation", tmp_path)


def test_the_chain_sends_only_what_e2_rejects_to_e5(tmp_path):
    from evaluation.cord_gate import hybrid_table, run_hybrid

    e2 = {"a": record(GOOD, True), "b": record(line_items([item("CAKE", "9000")], ["no total"]), True),
          "c": record(line_items([], ["no items"]), True)}
    e5 = {"a": record(line_items([]), False),  # never asked: E2 accepted it
          "b": record(GOOD, False), "c": record(line_items([], ["no items"]), True)}
    for mode, records in (("e2e", e2), ("e5", e5)):
        with (tmp_path / f"{mode}-validation.receipts.jsonl").open("w", encoding="utf-8") as file:
            for receipt_id, entry in records.items():
                file.write(json.dumps({"id": receipt_id, **entry}) + "\n")
        (tmp_path / f"{mode}-validation.json").write_text(json.dumps({"seconds_per_receipt": 2.0 if mode == "e2e" else 30.0}))

    summary = run_hybrid("validation", tmp_path)["standard"]

    assert (summary["accepted_by_e2"], summary["sent_to_e5"], summary["accepted_by_e5"], summary["to_person"]) == (1, 2, 1, 1)
    assert summary["wrong_by_e5"] == 1
    assert summary["accepted_error_rate"] == 0.5
    assert summary["seconds_per_receipt"] == 2.0 + 30.0 * 2 / 3
    assert "| validation | standard | 3 | 1 (0 wrong) | 2 | 1 (1 wrong) | 1 |" in hybrid_table(tmp_path)
