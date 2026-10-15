"""Export after approval, through the service, REST and the web page, to the real mock ERP."""

import pytest
from fastapi.testclient import TestClient

from intake.app import create_app
from intake.service import InvalidTransition, ValidationProblem
from intake.status import InvoiceStatus

from .erp_fakes import Erp, LosesFirstResponse
from .services import make_service

WRONG_NET = (b"<ram:TaxBasisTotalAmount>230.00", b"<ram:TaxBasisTotalAmount>240.00")


@pytest.fixture
def erp(tmp_path):
    return Erp(tmp_path)


@pytest.fixture
def service(tmp_path, erp):
    return make_service(tmp_path, erp=erp.client())


def test_auto_approved_invoice_is_exported(service, erp, ubl_xml):
    record = service.ingest(ubl_xml, "x.xml")
    assert record.status is InvoiceStatus.EXPORTED
    assert record.erp_id == "ERP-000001"
    assert [e.new_status for e in record.events][-2:] == ["auto_approved", "exported"]
    assert record.events[-1].note == "ERP id ERP-000001"
    (stored,) = erp.invoices()
    assert stored["idempotency_key"] == record.id
    assert stored["invoice"]["invoice"]["gross_total"] == "2082.50"
    assert stored["invoice"]["approval"]["status"] == "auto_approved"
    assert stored["invoice"]["approval"]["by"] == "system"


def test_manual_approval_is_exported(service, erp, cii_xml):
    held = service.ingest(cii_xml.replace(*WRONG_NET), "x.xml")
    assert held.status is InvoiceStatus.NEEDS_REVIEW
    assert erp.invoices() == []
    record = service.approve(held.id, "web:Anna", "checked with seller")
    assert record.status is InvoiceStatus.EXPORTED
    approval = erp.invoices()[0]["invoice"]["approval"]
    assert (approval["status"], approval["by"], approval["note"]) == (
        "approved",
        "web:Anna",
        "checked with seller",
    )


def test_review_invoices_are_not_exported(service, erp, cii_xml):
    held = service.ingest(cii_xml.replace(*WRONG_NET), "x.xml")
    with pytest.raises(InvalidTransition, match="needs_review"):
        service.export(held.id, "web:Anna")
    assert erp.invoices() == []


def test_erp_down_then_retry(service, erp, ubl_xml):
    erp.set_fail_every(1)  # every request fails
    record = service.ingest(ubl_xml, "x.xml")
    assert record.status is InvoiceStatus.EXPORT_FAILED
    (attempt,) = record.exports
    assert (attempt.ok, attempt.attempts) == (False, 4)
    assert "not reachable after 4 attempts: HTTP 503" in attempt.error
    assert erp.sleeps == [0.5, 1.0, 2.0]

    erp.set_fail_every(0)
    retried = service.export(record.id, "web:Anna")
    assert retried.status is InvoiceStatus.EXPORTED
    assert [x.ok for x in retried.exports] == [False, True]
    assert retried.events[-1].actor == "web:Anna"
    assert len(erp.invoices()) == 1


def test_flaky_erp_is_handled_by_retries(service, erp, ubl_xml, cii_xml):
    erp.set_fail_every(2)  # the 2nd request fails, the retry succeeds
    service.ingest(ubl_xml, "a.xml")
    second = service.ingest(cii_xml, "b.xml")
    assert second.status is InvoiceStatus.EXPORTED
    assert second.exports[-1].attempts == 2
    assert len(erp.invoices()) == 2


def test_lost_response_does_not_create_a_duplicate(tmp_path, erp, ubl_xml):
    """The ERP stored the invoice but the answer got lost; the retry must not store it again."""
    lossy = LosesFirstResponse(erp.http)
    service = make_service(tmp_path, erp=erp.client(lossy))
    record = service.ingest(ubl_xml, "x.xml")
    assert lossy.calls == 2
    assert record.status is InvoiceStatus.EXPORTED
    assert record.events[-1].note == "ERP id ERP-000001 (the ERP already had it)"
    assert len(erp.invoices()) == 1


def test_export_twice_is_refused(service, erp, ubl_xml):
    record = service.ingest(ubl_xml, "x.xml")
    with pytest.raises(InvalidTransition, match="exported"):
        service.export(record.id, "web:Anna")
    assert len(erp.invoices()) == 1


def test_without_erp_approved_invoices_stay_approved(tmp_path, ubl_xml):
    service = make_service(tmp_path)  # no ERP configured
    record = service.ingest(ubl_xml, "x.xml")
    assert record.status is InvoiceStatus.AUTO_APPROVED
    with pytest.raises(ValidationProblem, match="No ERP is configured"):
        service.export(record.id, "web:Anna")


# --- REST and web ----------------------------------------------------------------------


def test_rest_export_retry(service, erp, ubl_xml):
    client = TestClient(create_app(service))
    erp.set_fail_every(1)
    invoice_id = client.post("/invoices", files={"file": ("x.xml", ubl_xml)}).json()["id"]
    assert client.get(f"/invoices/{invoice_id}").json()["status"] == "export_failed"
    erp.set_fail_every(0)
    response = client.post(f"/invoices/{invoice_id}/export", json={"reviewer": "Anna"})
    assert response.status_code == 200
    assert response.json()["status"] == "exported"
    assert response.json()["erp_id"] == "ERP-000001"
    again = client.post(f"/invoices/{invoice_id}/export", json={"reviewer": "Anna"})
    assert again.status_code == 409


def test_web_retry_button(service, erp, ubl_xml):
    client = TestClient(create_app(service), follow_redirects=False)
    erp.set_fail_every(1)
    record = service.ingest(ubl_xml, "x.xml")
    page = client.get(f"/review/{record.id}").text
    assert "Retry export" in page
    assert "not reachable after 4 attempts" in page
    erp.set_fail_every(0)
    response = client.post(f"/review/{record.id}/export", data={"reviewer": "Anna"})
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "ERP-000001" in page
    assert "Retry export" not in page
