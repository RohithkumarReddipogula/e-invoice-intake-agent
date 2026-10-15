"""Service-level review actions: correct, approve, reject."""

from decimal import Decimal

import pytest

from intake.rules import RuleStatus
from intake.service import InvalidTransition, NotFound, ValidationProblem
from intake.status import InvoiceStatus

from .conftest import render_pdf
from .llm_fakes import INVOICE_LINES, answer
from .services import make_service

WRONG_NET = (b"<ram:TaxBasisTotalAmount>230.00", b"<ram:TaxBasisTotalAmount>240.00")


@pytest.fixture
def service(tmp_path):
    return make_service(tmp_path)


@pytest.fixture
def low_confidence(tmp_path):
    """A plain-PDF invoice held only because buyer_name has low confidence."""
    output = answer(buyer_name={"value": "Beispiel Handels AG", "confidence": 0.5})
    service = make_service(tmp_path, llm_output=output)
    return service, service.ingest(render_pdf(INVOICE_LINES), "x.pdf")


@pytest.fixture
def wrong_net(service, cii_xml):
    """A CII invoice held because R1 fails (net total 240.00 instead of 230.00)."""
    return service.ingest(cii_xml.replace(*WRONG_NET), "x.xml")


def rule(record, rule_id):
    return next(r for r in record.rules if r.rule_id == rule_id)


# --- correct ---------------------------------------------------------------------------


def test_correction_makes_the_field_confident(low_confidence):
    service, record = low_confidence
    after = service.correct_fields(
        record.id, {"buyer_name": "Beispiel Handels AG (Hamburg)"}, "web:Anna"
    )
    assert after.status is InvoiceStatus.NEEDS_REVIEW  # approving is a separate step
    assert after.data.buyer_name == "Beispiel Handels AG (Hamburg)"
    assert after.confidence["buyer_name"] == 1.0
    assert after.corrected_by == {"buyer_name": "web:Anna"}
    assert after.reasons == []
    last = after.events[-1]
    assert (last.actor, last.new_status, last.note) == (
        "web:Anna",
        "needs_review",
        "corrected: buyer_name",
    )


def test_correction_reruns_the_rules(service, wrong_net):
    assert rule(wrong_net, "R1").status is RuleStatus.FAIL
    after = service.correct_fields(wrong_net.id, {"net_total": "230.00"}, "web:Anna")
    assert after.data.net_total == Decimal("230.00")
    assert rule(after, "R1").status is RuleStatus.PASS
    assert after.reasons == []
    assert rule(after, "R7").status is RuleStatus.PASS  # kept from the original check


def test_correction_can_create_a_new_problem(service, wrong_net):
    after = service.correct_fields(
        wrong_net.id, {"net_total": "230.00", "gross_total": "999.00"}, "web:Anna"
    )
    assert rule(after, "R2").status is RuleStatus.FAIL
    assert any(r.startswith("R2 net_vat_gross failed") for r in after.reasons)


def test_earlier_corrections_are_kept(low_confidence):
    service, record = low_confidence
    service.correct_fields(record.id, {"buyer_name": "B AG"}, "web:Anna")
    after = service.correct_fields(record.id, {"seller_name": "Muster Software GmbH"}, "web:Ben")
    # seller_name did not change, so only Anna's correction is recorded.
    assert after.corrected_by == {"buyer_name": "web:Anna"}


def test_unchanged_values_are_a_no_op(low_confidence):
    service, record = low_confidence
    after = service.correct_fields(record.id, {"invoice_number": "RE-2026-0042"}, "web:Anna")
    assert after.events == record.events


def test_invalid_value(low_confidence):
    service, record = low_confidence
    with pytest.raises(ValidationProblem, match="issue_date"):
        service.correct_fields(record.id, {"issue_date": "15.09.2026"}, "web:Anna")


def test_unknown_field(low_confidence):
    service, record = low_confidence
    with pytest.raises(ValidationProblem, match="Unknown field"):
        service.correct_fields(record.id, {"total": "1"}, "web:Anna")


def test_auto_approved_invoice_cannot_be_corrected(service, ubl_xml):
    record = service.ingest(ubl_xml, "x.xml")
    with pytest.raises(InvalidTransition, match="auto_approved"):
        service.correct_fields(record.id, {"buyer_name": "X"}, "web:Anna")


def test_scan_can_be_typed_in_by_hand(service):
    scan = service.ingest(render_pdf([]), "scan.pdf")
    assert scan.data is None
    values = {
        "invoice_number": "R-1",
        "issue_date": "2026-10-01",
        "currency": "EUR",
        "seller_name": "Muster Software GmbH",
        "seller_vat_id": "DE123456789",
        "buyer_name": "Beispiel Handels AG",
        "line_items": [{"description": "Work", "net_amount": "100.00"}],
        "net_total": "100.00",
        "vat_total": "19.00",
        "gross_total": "119.00",
    }
    after = service.correct_fields(scan.id, values, "web:Anna")
    assert after.source == "manual"
    assert all(rule.status is not RuleStatus.FAIL for rule in after.rules)
    # Still blocking (no text layer), so approving needs a note.
    assert after.reasons[0] == "PDF has no text layer (scan); OCR is not supported."
    with pytest.raises(ValidationProblem, match="note is required"):
        service.approve(scan.id, "web:Anna")
    assert (
        service.approve(scan.id, "web:Anna", "typed in from paper").status is InvoiceStatus.APPROVED
    )


# --- approve ---------------------------------------------------------------------------


def test_approve_low_confidence_needs_no_note(low_confidence):
    service, record = low_confidence
    after = service.approve(record.id, "web:Anna")
    assert after.status is InvoiceStatus.APPROVED
    assert after.events[-1].actor == "web:Anna"


def test_approve_with_failed_rule_needs_a_note(service, wrong_net):
    with pytest.raises(ValidationProblem, match=r"note is required .*\(R1, R2\)"):
        service.approve(wrong_net.id, "web:Anna", "   ")
    after = service.approve(wrong_net.id, "web:Anna", "Net total confirmed by phone.")
    assert after.status is InvoiceStatus.APPROVED
    assert after.events[-1].note == "Net total confirmed by phone."


def test_cannot_approve_twice(low_confidence):
    service, record = low_confidence
    service.approve(record.id, "web:Anna")
    with pytest.raises(InvalidTransition):
        service.approve(record.id, "web:Ben")


def test_cannot_approve_without_data(service):
    scan = service.ingest(render_pdf([]), "scan.pdf")
    with pytest.raises(ValidationProblem, match="no invoice data"):
        service.approve(scan.id, "web:Anna", "looks fine")


def test_unknown_invoice(service):
    with pytest.raises(NotFound):
        service.approve("nope", "web:Anna")


# --- reject ----------------------------------------------------------------------------


def test_reject_needs_a_reason(service, wrong_net):
    with pytest.raises(ValidationProblem, match="reason is required"):
        service.reject(wrong_net.id, "web:Anna", "  ")
    after = service.reject(wrong_net.id, "web:Anna", "Wrong totals, asked seller for a new one.")
    assert after.status is InvoiceStatus.REJECTED
    assert after.events[-1].note == "Wrong totals, asked seller for a new one."


def test_auto_approved_cannot_be_rejected(service, ubl_xml):
    record = service.ingest(ubl_xml, "x.xml")
    with pytest.raises(InvalidTransition):
        service.reject(record.id, "web:Anna", "no")
