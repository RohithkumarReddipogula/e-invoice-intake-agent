"""INTAKE_MAX_AUTO_AMOUNT and INTAKE_REVIEW_NEW_SUPPLIERS: off by default, tested when on."""

from decimal import Decimal

import pytest

from intake.config import Settings
from intake.status import InvoiceStatus

from .services import make_service
from .test_mcp import call

# The UBL fixture: Muster Software GmbH, DE123456789, gross 2082.50.
OTHER_NUMBER = (b"<cbc:ID>RE-2026-0042</cbc:ID>", b"<cbc:ID>RE-2026-0043</cbc:ID>")
THIRD_NUMBER = (b"<cbc:ID>RE-2026-0042</cbc:ID>", b"<cbc:ID>RE-2026-0044</cbc:ID>")
OTHER_IBAN = (b"DE89370400440532013000", b"DE02120300000000202051")


def test_both_are_off_by_default(monkeypatch):
    monkeypatch.delenv("INTAKE_MAX_AUTO_AMOUNT", raising=False)
    monkeypatch.delenv("INTAKE_REVIEW_NEW_SUPPLIERS", raising=False)
    s = Settings(_env_file=None)
    assert s.max_auto_amount is None
    assert s.review_new_suppliers is False


def test_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("INTAKE_MAX_AUTO_AMOUNT", "5000.50")
    monkeypatch.setenv("INTAKE_REVIEW_NEW_SUPPLIERS", "1")
    s = Settings(_env_file=None)
    assert s.max_auto_amount == Decimal("5000.50")
    assert s.review_new_suppliers is True
    monkeypatch.setenv("INTAKE_MAX_AUTO_AMOUNT", "")  # empty, as in .env.example
    assert Settings(_env_file=None).max_auto_amount is None
    with pytest.raises(ValueError):
        Settings(_env_file=None, max_auto_amount=Decimal("0"))


def test_off_means_unchanged(tmp_path, ubl_xml):
    assert make_service(tmp_path).ingest(ubl_xml, "x.xml").status is InvoiceStatus.AUTO_APPROVED


# --- amount limit ----------------------------------------------------------------------


def test_above_the_limit_goes_to_review(tmp_path, ubl_xml):
    service = make_service(tmp_path, max_auto_amount=Decimal("1000"))
    record = service.ingest(ubl_xml, "x.xml")
    assert record.status is InvoiceStatus.NEEDS_REVIEW
    assert record.blocking == [
        "Gross total 2,082.50 is above the auto-approval limit of 1,000.00 "
        "(INTAKE_MAX_AUTO_AMOUNT): a person checks it."
    ]
    assert all(r.status.value != "fail" for r in record.rules)  # no rule failed
    assert not record.held_for_confidence_only  # MCP may not approve it


def test_exactly_at_the_limit_is_allowed(tmp_path, ubl_xml):
    service = make_service(tmp_path, max_auto_amount=Decimal("2082.50"))
    assert service.ingest(ubl_xml, "x.xml").status is InvoiceStatus.AUTO_APPROVED


def test_a_person_can_approve_above_the_limit_with_a_note(tmp_path, ubl_xml):
    service = make_service(tmp_path, max_auto_amount=Decimal("1000"))
    record = service.ingest(ubl_xml, "x.xml")
    approved = service.approve(record.id, "web:Anna", "Checked against the order.")
    assert approved.status is InvoiceStatus.APPROVED


def test_mcp_cannot_approve_above_the_limit(tmp_path, ubl_xml):
    service = make_service(tmp_path, max_auto_amount=Decimal("1000"))
    record = service.ingest(ubl_xml, "x.xml")
    error, text = call(service, "approve_invoice", {"invoice_id": record.id, "note": "ok"})
    assert error and "blocking problem" in text
    assert service.get(record.id).status is InvoiceStatus.NEEDS_REVIEW


# --- new suppliers ---------------------------------------------------------------------


@pytest.fixture
def careful(tmp_path):
    return make_service(tmp_path, review_new_suppliers=True)


def test_first_invoice_of_a_new_supplier_goes_to_review(careful, ubl_xml):
    record = careful.ingest(ubl_xml, "x.xml")
    assert record.status is InvoiceStatus.NEEDS_REVIEW
    assert record.blocking[0].startswith("New supplier: no accepted invoice from this seller yet.")


def test_known_supplier_is_auto_approved_after_acceptance(careful, ubl_xml):
    first = careful.ingest(ubl_xml, "first.xml")
    careful.approve(first.id, "web:Anna", "New supplier checked: contract on file.")
    second = careful.ingest(ubl_xml.replace(*OTHER_NUMBER), "second.xml")
    assert second.status is InvoiceStatus.AUTO_APPROVED
    assert second.blocking == []


def test_changed_iban_of_a_known_supplier_goes_to_review(careful, ubl_xml):
    first = careful.ingest(ubl_xml, "first.xml")
    careful.approve(first.id, "web:Anna", "checked")
    changed = careful.ingest(ubl_xml.replace(*OTHER_NUMBER).replace(*OTHER_IBAN), "new-bank.xml")
    assert changed.status is InvoiceStatus.NEEDS_REVIEW
    assert changed.blocking[0].startswith("Bank details changed: IBAN DE02120300000000202051")


def test_waiting_or_rejected_invoices_do_not_make_a_supplier_known(careful, ubl_xml):
    first = careful.ingest(ubl_xml, "first.xml")  # still in review: not accepted
    second = careful.ingest(ubl_xml.replace(*OTHER_NUMBER), "second.xml")
    assert second.blocking[0].startswith("New supplier")
    careful.reject(first.id, "web:Anna", "unknown company")
    careful.reject(second.id, "web:Anna", "unknown company")
    third = careful.ingest(ubl_xml.replace(*THIRD_NUMBER), "third.xml")
    assert third.blocking[0].startswith("New supplier")


def test_supplier_known_by_name_when_vat_id_is_missing(careful, ubl_xml):
    """Matched by either key, like R6: an accepted invoice with VAT ID makes the name known."""
    from .services import UBL_VAT_BLOCK

    first = careful.ingest(ubl_xml, "first.xml")
    careful.approve(first.id, "web:Anna", "checked")
    no_vat = careful.ingest(ubl_xml.replace(*OTHER_NUMBER).replace(UBL_VAT_BLOCK, b""), "x.xml")
    assert not any(b.startswith("New supplier") for b in no_vat.blocking)


# --- the eval keeps them off -----------------------------------------------------------


def test_eval_runner_turns_guardrails_off(tmp_path, tmp_path_factory):
    from eval.generate import write_split
    from eval.run import run_split

    from .check_results import load_check_result

    out = tmp_path_factory.mktemp("evaldata")
    write_split(out, "dev", seed=2026)
    on = Settings(
        _env_file=None,
        llm_cache_dir=tmp_path / "cache",
        max_auto_amount=Decimal("1"),
        review_new_suppliers=True,
    )
    rows = run_split(out / "dev", on, lambda path: load_check_result("valid"), live=False)
    structured_clean = [r for r in rows if r.kind != "plain_pdf" and not r.planted]
    assert structured_clean
    # With either guardrail active, every one of these would be held.
    assert all(r.status == "auto_approved" for r in structured_clean)
    assert all(not r.blocking for r in structured_clean)
