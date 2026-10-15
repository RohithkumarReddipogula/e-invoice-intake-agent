"""REST API with FastAPI's TestClient."""

import pytest
from fastapi.testclient import TestClient

from intake.app import create_app

from .services import make_service

WRONG_NET = (b"<ram:TaxBasisTotalAmount>230.00", b"<ram:TaxBasisTotalAmount>240.00")


@pytest.fixture
def service(tmp_path):
    return make_service(tmp_path)


@pytest.fixture
def client(service):
    return TestClient(create_app(service))


def upload(client, content: bytes, name: str = "invoice.xml"):
    return client.post("/invoices", files={"file": (name, content)})


@pytest.fixture
def held(client, cii_xml):
    response = upload(client, cii_xml.replace(*WRONG_NET))
    assert response.status_code == 201
    return response.json()


def test_upload_and_read(client, ubl_xml):
    response = upload(client, ubl_xml)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "auto_approved"
    assert body["data"]["gross_total"] == "2082.50"  # Decimal as string, no float rounding
    assert body["held_for_confidence_only"] is False
    assert body["events"][0]["actor"] == "api"

    assert client.get(f"/invoices/{body['id']}").json()["id"] == body["id"]
    listed = client.get("/invoices").json()
    assert [i["id"] for i in listed] == [body["id"]]


def test_list_filter_and_limit(client, ubl_xml, cii_xml):
    upload(client, ubl_xml)
    upload(client, cii_xml.replace(*WRONG_NET))
    review = client.get("/invoices", params={"status": "needs_review"}).json()
    assert len(review) == 1
    assert review[0]["main_reason"].startswith("R1 lines_sum failed")
    assert client.get("/invoices", params={"limit": 101}).status_code == 422
    assert client.get("/invoices", params={"status": "nonsense"}).status_code == 422


@pytest.mark.parametrize(
    ("content", "detail"),
    [(b"", "empty"), (b"not an invoice", "not well-formed XML")],
)
def test_upload_rejected(client, content, detail):
    response = upload(client, content)
    assert response.status_code == 422
    assert detail in response.json()["detail"]


def test_upload_too_large(client, service, ubl_xml):
    service.settings.max_upload_bytes = 1000
    response = upload(client, ubl_xml)
    assert response.status_code == 422
    assert "larger than" in response.json()["detail"]


def test_unknown_invoice(client):
    assert client.get("/invoices/nope").status_code == 404
    assert client.post("/invoices/nope/approve", json={"reviewer": "Anna"}).status_code == 404


def test_correct_fields(client, held):
    response = client.patch(
        f"/invoices/{held['id']}/fields",
        json={"reviewer": "Anna", "fields": {"net_total": "230.00"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reasons"] == []
    assert body["corrected_by"] == {"net_total": "api:Anna"}


def test_correct_invalid_value(client, held):
    response = client.patch(
        f"/invoices/{held['id']}/fields",
        json={"reviewer": "Anna", "fields": {"net_total": "two hundred"}},
    )
    assert response.status_code == 422
    assert "net_total" in response.json()["detail"]


def test_approve_needs_a_note_when_a_rule_fails(client, held):
    url = f"/invoices/{held['id']}/approve"
    missing = client.post(url, json={"reviewer": "Anna"})
    assert missing.status_code == 422
    assert "note is required" in missing.json()["detail"]
    ok = client.post(url, json={"reviewer": "Anna", "note": "checked with seller"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"
    assert ok.json()["events"][-1]["actor"] == "api:Anna"
    again = client.post(url, json={"reviewer": "Anna", "note": "again"})
    assert again.status_code == 409


def test_reject(client, held):
    url = f"/invoices/{held['id']}/reject"
    assert client.post(url, json={"reviewer": "Anna", "reason": ""}).status_code == 422
    response = client.post(url, json={"reviewer": "Anna", "reason": "duplicate by mail"})
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


@pytest.mark.parametrize("reviewer", ["", "   "])
def test_reviewer_is_required(client, held, reviewer):
    response = client.post(f"/invoices/{held['id']}/approve", json={"reviewer": reviewer})
    assert response.status_code == 422


def test_xml_file_is_served_as_plain_text(client, ubl_xml):
    invoice_id = upload(client, ubl_xml).json()["id"]
    response = client.get(f"/invoices/{invoice_id}/file")
    assert response.status_code == 200
    assert response.content == ubl_xml
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_pdf_file_is_served_sandboxed(client, zugferd_pdf):
    invoice_id = upload(client, zugferd_pdf, "invoice.pdf").json()["id"]
    response = client.get(f"/invoices/{invoice_id}/file")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-security-policy"] == "sandbox"


def test_cross_origin_post_is_refused(client, held):
    url = f"/invoices/{held['id']}/approve"
    body = {"reviewer": "Anna", "note": "x"}
    evil = client.post(url, json=body, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    evil_referer = client.post(url, json=body, headers={"Referer": "https://evil.example/x"})
    assert evil_referer.status_code == 403
    assert client.get(f"/invoices/{held['id']}").json()["status"] == "needs_review"
    same = client.post(url, json=body, headers={"Origin": "http://testserver"})
    assert same.status_code == 200
