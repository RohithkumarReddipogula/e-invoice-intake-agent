"""Review web page: queue, detail and the three forms."""

import re
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from intake.app import create_app
from intake.status import InvoiceStatus
from intake.web import local_time

from .conftest import render_pdf
from .llm_fakes import INVOICE_LINES, answer
from .services import make_service

WRONG_NET = (b"<ram:TaxBasisTotalAmount>230.00", b"<ram:TaxBasisTotalAmount>240.00")


@pytest.fixture
def service(tmp_path):
    return make_service(tmp_path)


@pytest.fixture
def client(service):
    return TestClient(create_app(service), follow_redirects=False)


@pytest.fixture
def held(service, cii_xml):
    return service.ingest(cii_xml.replace(*WRONG_NET), "wrong-net.xml")


def form_values(html: str) -> dict[str, str]:
    """The values the browser would submit from the corrections form."""
    form = html.split('action="/review/', 1)[1].split("</form>", 1)[0]
    values = {}
    for tag in re.findall(r"<input[^>]*>", form):
        name = re.search(r'name="([^"]+)"', tag)
        if name is None or "disabled" in tag:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        values[name.group(1)] = value.group(1) if value else ""
    return values


def test_root_redirects_to_queue(client):
    response = client.get("/")
    assert response.status_code == 307
    assert response.headers["location"] == "/review"


def test_empty_queue(client):
    response = client.get("/review")
    assert response.status_code == 200
    assert "Nothing to review." in response.text


def test_queue_shows_the_main_reason(client, held):
    html = client.get("/review").text
    assert f'href="/review/{held.id}"' in html
    assert "2026/INV/117" in html
    assert "R1 lines_sum failed: lines 240.00 - allowances 10.00 = 230.00" in html


def test_detail_shows_reasons_checks_and_history(client, held):
    html = client.get(f"/review/{held.id}").text
    assert "Why this invoice needs a person" in html
    assert "but the net total is 240.00." in html
    assert '<span class="badge fail">fail</span>' in html
    assert "→ needs_review" in html
    assert "Approve" in html and "Reject" in html
    assert "&lt;rsm:CrossIndustryInvoice" in html  # XML shown escaped as text


def test_low_confidence_field_is_highlighted(service, tmp_path):
    output = answer(buyer_name={"value": "Beispiel Handels AG", "confidence": 0.5})
    svc = make_service(tmp_path / "other", llm_output=output)
    record = svc.ingest(render_pdf(INVOICE_LINES), "x.pdf")
    html = TestClient(create_app(svc)).get(f"/review/{record.id}").text
    row = html.split('for="f-buyer_name"', 1)[0].rsplit("<tr", 1)[1]
    assert 'class="low"' in row
    assert f'src="/invoices/{record.id}/file"' in html  # PDF shown in a frame


def test_untrusted_text_is_escaped(service, client, ubl_xml):
    evil = ubl_xml.replace(b">Muster Software GmbH<", b">&lt;script&gt;alert(1)&lt;/script&gt;<")
    record = service.ingest(evil.replace(b">2082.50<", b">1.00<", 1), "<img src=x>.xml")
    html = client.get(f"/review/{record.id}").text
    assert "<script>alert(1)" not in html
    assert "<img src=x>" not in html
    assert "&lt;script&gt;alert(1)" in html


def test_unchanged_form_changes_nothing(client, held):
    """The form shows stored values in a format that parses back to the same values."""
    html = client.get(f"/review/{held.id}").text
    values = form_values(html) | {"reviewer": "Anna"}
    response = client.post(f"/review/{held.id}/fields", data=values)
    assert response.status_code == 303
    assert response.headers["location"] == f"/review/{held.id}?done=unchanged"


def test_correct_via_form(client, held):
    html = client.get(f"/review/{held.id}").text
    values = form_values(html) | {"reviewer": "Anna", "net_total": "230.00"}
    response = client.post(f"/review/{held.id}/fields", data=values)
    assert response.status_code == 303
    assert response.headers["location"].endswith("?done=corrected")
    assert "reviewer=Anna" in response.headers["set-cookie"]
    page = client.get(response.headers["location"]).text
    assert "Corrections saved." in page
    assert "Why this invoice needs a person" not in page
    assert "corrected by web:Anna" in page


def test_add_a_line_in_an_empty_row(client, held, service):
    html = client.get(f"/review/{held.id}").text
    values = form_values(html) | {
        "reviewer": "Anna",
        "line-2-description": "Extra",
        "line-2-net_amount": "10.00",
    }
    client.post(f"/review/{held.id}/fields", data=values)
    lines = service.get(held.id).data.line_items
    assert [line.description for line in lines][-1] == "Extra"


def test_invalid_correction_shows_the_error(client, held):
    html = client.get(f"/review/{held.id}").text
    values = form_values(html) | {"reviewer": "Anna", "net_total": "abc"}
    response = client.post(f"/review/{held.id}/fields", data=values)
    assert response.status_code == 400
    assert 'class="error"' in response.text and "net_total" in response.text


def test_invalid_correction_keeps_the_entered_values(client, held, service):
    html = client.get(f"/review/{held.id}").text
    values = form_values(html) | {
        "reviewer": "Anna Example",
        "net_total": "abc",
        "buyer_name": "Changed Buyer AG",
        "line-0-description": "Edited line",
        "line-2-description": "New line",
    }
    response = client.post(f"/review/{held.id}/fields", data=values)
    assert response.status_code == 400
    assert "net_total" in response.text  # the error message
    for typed in [
        'value="abc"',
        'value="Changed Buyer AG"',
        'value="Edited line"',
        'value="New line"',
        'value="Anna Example"',
    ]:
        assert typed in response.text, typed
    # The same row count comes back, so the next submit lines up.
    assert form_values(response.text)["line_count"] == values["line_count"]
    # Nothing was stored.
    stored = service.get(held.id)
    assert stored.data.buyer_name == "Beispiel Handels AG"
    assert stored.events == held.events


def test_missing_name_keeps_the_entered_values(client, held):
    html = client.get(f"/review/{held.id}").text
    values = form_values(html) | {"reviewer": " ", "buyer_name": "Changed Buyer AG"}
    response = client.post(f"/review/{held.id}/fields", data=values)
    assert response.status_code == 400
    assert "Enter your name" in response.text
    assert 'value="Changed Buyer AG"' in response.text


def test_fixed_form_can_be_resubmitted(client, held, service):
    html = client.get(f"/review/{held.id}").text
    bad = form_values(html) | {"reviewer": "Anna", "net_total": "abc", "buyer_name": "B AG"}
    retry = form_values(client.post(f"/review/{held.id}/fields", data=bad).text)
    response = client.post(f"/review/{held.id}/fields", data=retry | {"net_total": "230.00"})
    assert response.status_code == 303
    stored = service.get(held.id)
    assert (stored.data.buyer_name, str(stored.data.net_total)) == ("B AG", "230.00")


@pytest.mark.parametrize(
    ("iso", "shown"),
    [
        ("2026-10-15T18:14:02+00:00", "2026-10-15 20:14 CEST"),  # summer time, UTC+2
        ("2026-01-15T12:00:00+00:00", "2026-01-15 13:00 CET"),  # winter time, UTC+1
        ("2026-12-31T23:30:00+00:00", "2027-01-01 00:30 CET"),  # crosses midnight and year
    ],
)
def test_local_time(iso, shown):
    assert local_time(iso, ZoneInfo("Europe/Berlin")) == shown


def test_pages_show_berlin_time(client, held):
    created = held.created_at
    expected = local_time(created, ZoneInfo("Europe/Berlin"))
    assert expected.endswith((" CET", " CEST"))
    assert expected in client.get("/review").text
    detail = client.get(f"/review/{held.id}").text
    assert f"received {expected}" in detail


def test_approve_needs_name_and_note(client, held, service):
    url = f"/review/{held.id}/approve"
    no_name = client.post(url, data={"reviewer": "", "note": "ok"})
    assert no_name.status_code == 400
    assert "Enter your name" in no_name.text
    no_note = client.post(url, data={"reviewer": "Anna", "note": ""})
    assert no_note.status_code == 400
    assert "note is required" in no_note.text
    ok = client.post(url, data={"reviewer": "Anna", "note": "confirmed"})
    assert ok.status_code == 303
    assert service.get(held.id).status is InvoiceStatus.APPROVED


def test_reject_via_form(client, held, service):
    response = client.post(
        f"/review/{held.id}/reject", data={"reviewer": "Anna", "reason": "wrong totals"}
    )
    assert response.status_code == 303
    assert service.get(held.id).status is InvoiceStatus.REJECTED


def test_decided_invoice_has_no_forms(client, service, ubl_xml):
    record = service.ingest(ubl_xml, "x.xml")
    html = client.get(f"/review/{record.id}").text
    assert ">Approve<" not in html
    assert "Save corrections" not in html


def test_unknown_invoice_page(client):
    response = client.get("/review/nope")
    assert response.status_code == 404
    assert "Invoice not found" in response.text


def test_cross_site_form_post_is_refused(client, held, service):
    response = client.post(
        f"/review/{held.id}/approve",
        data={"reviewer": "Mallory", "note": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert service.get(held.id).status is InvoiceStatus.NEEDS_REVIEW
