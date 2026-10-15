"""End to end through the pipeline and SQLite, with a fake validator and a fake LLM."""

import pytest

from intake import db
from intake.pipeline import UploadRejected
from intake.rules import RuleStatus
from intake.service import IntakeService, NotFound
from intake.status import InvoiceStatus

from .conftest import render_pdf
from .llm_fakes import INVOICE_LINES, answer
from .services import UBL_VAT_BLOCK, make_service


@pytest.fixture
def service(tmp_path) -> IntakeService:
    return make_service(tmp_path)


def rule(record, rule_id):
    return next(r for r in record.rules if r.rule_id == rule_id)


# --- structured invoices ---------------------------------------------------------------


def test_valid_xml_is_auto_approved(service, ubl_xml, tmp_path):
    record = service.ingest(ubl_xml, "invoice.xml")
    assert record.status is InvoiceStatus.AUTO_APPROVED
    assert record.reasons == []
    assert record.source == "xml"
    assert record.data.invoice_number == "RE-2026-0042"
    assert [r.rule_id for r in record.rules] == ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]
    assert [(e.old_status, e.new_status) for e in record.events] == [
        (None, "received"),
        ("received", "extracted"),
        ("extracted", "auto_approved"),
    ]
    assert (tmp_path / "files" / record.file_sha256).read_bytes() == ubl_xml


def test_zugferd_is_auto_approved(service, zugferd_pdf):
    record = service.ingest(zugferd_pdf, "invoice.pdf")
    assert record.status is InvoiceStatus.AUTO_APPROVED
    assert record.input_kind == "zugferd_pdf"


def test_wrong_sum_goes_to_review_with_the_reason(service, cii_xml):
    xml = cii_xml.replace(b"<ram:TaxBasisTotalAmount>230.00", b"<ram:TaxBasisTotalAmount>240.00")
    record = service.ingest(xml, "invoice.xml")
    assert record.status is InvoiceStatus.NEEDS_REVIEW
    assert (
        "R1 lines_sum failed: lines 240.00 - allowances 10.00 = 230.00, "
        "but the net total is 240.00."
    ) in record.reasons
    assert not record.held_for_confidence_only


def test_kosit_rejection_goes_to_review(tmp_path, ubl_xml):
    record = make_service(tmp_path, check="invalid").ingest(ubl_xml, "invoice.xml")
    assert record.status is InvoiceStatus.NEEDS_REVIEW
    assert rule(record, "R7").status is RuleStatus.FAIL
    assert any("BR-CO-15" in r for r in record.reasons)


def test_validator_not_running_goes_to_review(tmp_path, ubl_xml):
    record = make_service(tmp_path, check="error").ingest(ubl_xml, "invoice.xml")
    assert record.status is InvoiceStatus.NEEDS_REVIEW
    assert "could not run" in rule(record, "R7").reason


def test_credit_note_goes_to_review(service, cii_xml):
    xml = cii_xml.replace(b"<ram:TypeCode>380</ram:TypeCode>", b"<ram:TypeCode>381</ram:TypeCode>")
    record = service.ingest(xml, "credit.xml")
    assert record.status is InvoiceStatus.NEEDS_REVIEW
    assert record.blocking == ["Credit notes are not supported yet."]
    assert record.reasons[0] == "Credit notes are not supported yet."


# --- R6 duplicates with two keys -------------------------------------------------------


def test_same_file_twice_is_a_duplicate(service, ubl_xml):
    first = service.ingest(ubl_xml, "a.xml")
    second = service.ingest(ubl_xml, "b.xml")
    assert first.status is InvoiceStatus.AUTO_APPROVED
    assert second.status is InvoiceStatus.NEEDS_REVIEW
    assert rule(second, "R6").status is RuleStatus.FAIL


def test_first_with_vat_id_duplicate_without(service, ubl_xml):
    assert UBL_VAT_BLOCK in ubl_xml
    service.ingest(ubl_xml, "with-vat-id.xml")
    duplicate = service.ingest(ubl_xml.replace(UBL_VAT_BLOCK, b""), "without-vat-id.xml")
    assert duplicate.data.seller_vat_id is None
    r6 = rule(duplicate, "R6")
    assert r6.status is RuleStatus.FAIL
    assert "(same seller name)" in r6.reason


def test_first_without_vat_id_duplicate_with(service, ubl_xml):
    service.ingest(ubl_xml.replace(UBL_VAT_BLOCK, b""), "without-vat-id.xml")
    duplicate = service.ingest(ubl_xml, "with-vat-id.xml")
    assert rule(duplicate, "R6").status is RuleStatus.FAIL


def test_rejected_invoice_is_not_a_duplicate(service, ubl_xml):
    first = service.ingest(ubl_xml, "a.xml")
    with service.database.connect() as conn:
        db.set_status(conn, first.id, InvoiceStatus.REJECTED, actor="web:test", note="wrong")
    again = service.ingest(ubl_xml, "a-corrected.xml")
    assert rule(again, "R6").status is RuleStatus.PASS
    assert again.status is InvoiceStatus.AUTO_APPROVED


def test_needs_review_invoice_still_counts_for_duplicates(tmp_path, ubl_xml):
    reviewing = make_service(tmp_path, check="invalid")
    assert reviewing.ingest(ubl_xml, "a.xml").status is InvoiceStatus.NEEDS_REVIEW
    again = reviewing.ingest(ubl_xml, "b.xml")
    assert rule(again, "R6").status is RuleStatus.FAIL


# --- plain PDFs ------------------------------------------------------------------------


def test_plain_pdf_confident_is_auto_approved(service):
    record = service.ingest(render_pdf(INVOICE_LINES), "scan.pdf")
    assert record.source == "llm"
    assert rule(record, "R7").status is RuleStatus.SKIP
    assert record.status is InvoiceStatus.AUTO_APPROVED, record.reasons


def test_plain_pdf_low_confidence_is_held_for_confidence_only(tmp_path):
    output = answer(buyer_name={"value": "Beispiel Handels AG", "confidence": 0.5})
    record = make_service(tmp_path, llm_output=output).ingest(render_pdf(INVOICE_LINES), "x.pdf")
    assert record.status is InvoiceStatus.NEEDS_REVIEW
    assert record.reasons == ["Field `buyer_name` has low confidence (0.50): below the threshold."]
    assert record.held_for_confidence_only


def test_plain_pdf_without_text_goes_to_review(service):
    record = service.ingest(render_pdf([]), "scan.pdf")
    assert record.status is InvoiceStatus.NEEDS_REVIEW
    assert record.data is None
    assert record.blocking == ["PDF has no text layer (scan); OCR is not supported."]
    assert [e.new_status for e in record.events] == ["received", "needs_review"]
    assert not record.held_for_confidence_only


def test_llm_failure_goes_to_review(tmp_path):
    record = make_service(tmp_path, offline=True).ingest(render_pdf(INVOICE_LINES), "x.pdf")
    assert record.status is InvoiceStatus.NEEDS_REVIEW
    assert record.blocking[0].startswith("LLM extraction failed: No cached LLM answer")


# --- rejected uploads ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "empty"),
        (b"just some text", "not well-formed XML"),
        (b"<order/>", "Unknown XML document"),
    ],
)
def test_unsupported_upload_stores_nothing(service, content, message, tmp_path):
    with pytest.raises(UploadRejected, match=message):
        service.ingest(content, "x")
    assert service.list() == []
    assert not (tmp_path / "files").exists()


def test_too_large_upload(tmp_path, ubl_xml):
    service = make_service(tmp_path)
    service.pipeline.settings.max_upload_bytes = 100
    with pytest.raises(UploadRejected, match="larger than"):
        service.ingest(ubl_xml, "big.xml")


# --- service reads ---------------------------------------------------------------------


def test_list_and_filter(service, ubl_xml, cii_xml):
    service.ingest(ubl_xml, "a.xml")
    service.ingest(ubl_xml, "b.xml")  # duplicate -> review
    service.ingest(cii_xml, "c.xml")
    everything = service.list()
    assert len(everything) == 3
    review = service.list(status=InvoiceStatus.NEEDS_REVIEW)
    assert [s.invoice_number for s in review] == ["RE-2026-0042"]
    assert review[0].main_reason.startswith("R6 duplicate failed")
    assert len(service.list(limit=1)) == 1
    assert len(service.list(limit=10_000)) == 3  # capped, not an error


def test_get_unknown_invoice(service):
    with pytest.raises(NotFound):
        service.get("nope")


def test_migrations_run_once(tmp_path):
    database = db.Database(tmp_path / "x.db")
    db.Database(tmp_path / "x.db")  # second open must not re-run 001
    with database.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
