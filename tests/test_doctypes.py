"""Document types: the project's own, and the ones users define."""

import pytest

from extractor.review import review_notes
from extractor.schema import DocumentType, FieldSpec, doctype_names, load_doctype, save_doctype
from extractor.templates.values import find_values


@pytest.fixture(autouse=True)
def user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPLATES_DIR", str(tmp_path))
    return tmp_path


def order():
    return DocumentType(name="order", description="A purchase order.", fields=[
        FieldSpec(name="order_number", type="text"),
        FieldSpec(name="date", type="date"),
        FieldSpec(name="discount", type="amount", optional=True),
    ])


def test_the_project_comes_with_receipts_and_invoices():
    names = doctype_names()

    assert names["receipt"]["builtin"] and names["invoice"]["builtin"]
    invoice = load_doctype("invoice")
    assert "invoice_number" in invoice.field_names
    assert {f.name for f in invoice.fields if f.optional} == {"tax_id", "subtotal", "tax"}


def test_a_users_type_is_saved_and_loaded_by_name(user_dir):
    path = save_doctype(order())

    assert path == user_dir / "doctypes" / "order.yaml"
    assert load_doctype("order").fields[2].optional is True
    assert doctype_names()["order"]["builtin"] is False


def test_the_projects_types_cannot_be_replaced():
    with pytest.raises(FileExistsError):
        save_doctype(DocumentType(name="receipt", fields=[FieldSpec(name="total", type="amount")]))


def test_a_users_type_is_replaced_only_when_asked():
    save_doctype(order())

    with pytest.raises(FileExistsError):
        save_doctype(order())
    save_doctype(order(), replace=True)


@pytest.mark.parametrize("bad", [
    DocumentType(name="Order!", fields=[FieldSpec(name="date", type="date")]),
    DocumentType(name="order", fields=[]),
    DocumentType(name="order", fields=[FieldSpec(name="date", type="date"), FieldSpec(name="date", type="text")]),
    DocumentType(name="order", fields=[FieldSpec(name="Order No", type="text")]),
])
def test_names_are_checked(bad):
    with pytest.raises(ValueError):
        save_doctype(bad)


def test_an_unknown_type_is_not_found():
    with pytest.raises(FileNotFoundError):
        load_doctype("nothing")


def test_a_missing_optional_field_needs_no_look():
    notes = review_notes(order(), {"order_number": "A-1", "date": "01.02.2024", "discount": None})

    assert notes == {}
    assert review_notes(order(), {"order_number": None, "date": "01.02.2024"}) == {"order_number": "not found"}


def test_amounts_with_a_decimal_comma_are_found_whole():
    assert [found.value for found in find_values("GENEL TOPLAM 1.250,00 TL", "amount")] == ["1250.00"]
    assert [found.value for found in find_values("KDV %20 208,33", "amount")] == ["208.33"]
    assert [found.value for found in find_values("TOTAL RM 1,234.50", "amount")] == ["1234.50"]
