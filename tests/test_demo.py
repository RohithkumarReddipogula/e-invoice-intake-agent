"""Demo mode: the whole app with startup (self-check, reset, seed), the real mock ERP
in-process, and the committed caches. No API client may be created."""

import json
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import pytest
from fastapi.testclient import TestClient

from intake import demo
from intake.__main__ import bind_address
from intake.app import create_app
from intake.config import Settings
from intake.demo import SAMPLES, DemoSetupError
from intake.llm import LlmExtractor
from intake.service import IntakeService
from intake.status import InvoiceStatus

from .erp_fakes import Erp

BANNER = "Demo mode: synthetic invoices, cached results, resets hourly"
SEED_NUMBER = json.loads(Path("eval/data/test/test-026.truth.json").read_text())["truth"][
    "invoice_number"
]


@pytest.fixture(autouse=True)
def no_keys_and_no_network(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "INTAKE_DEMO_MODE"):
        monkeypatch.delenv(var, raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("a live LLM call was attempted in demo mode")

    # The only places a live LLM request can come from.
    monkeypatch.setattr(anthropic, "Anthropic", forbidden)
    monkeypatch.setattr(LlmExtractor, "_call", forbidden)


def demo_settings(tmp_path, **kwargs) -> Settings:
    return Settings(
        _env_file=None,
        demo_mode=True,
        database_path=tmp_path / "intake.db",
        files_dir=tmp_path / "files",
        erp_base_url="http://testserver",
        **kwargs,
    )


@pytest.fixture
def erp(tmp_path):
    return Erp(tmp_path, demo=True)


@pytest.fixture
def service(tmp_path, erp):
    svc = IntakeService.from_settings(demo_settings(tmp_path))
    svc.erp = erp.client()  # the in-process mock ERP instead of 127.0.0.1:8001
    return svc


@pytest.fixture
def client(service, erp):
    app = create_app(service, demo_erp_reset=lambda: erp.http.post("/api/reset"))
    with TestClient(app, follow_redirects=False) as c:  # runs startup: check, reset, seed
        yield c


def pick(client, sample_id):
    response = client.post(f"/demo/process/{sample_id}")
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/review/")[1].split("?")[0]


# --- settings and binding --------------------------------------------------------------


def test_off_by_default():
    s = Settings(_env_file=None)
    assert s.demo_mode is False
    assert bind_address(s, None) == ("127.0.0.1", 8000)
    assert bind_address(s, 9000) == ("127.0.0.1", 9000)


def test_binds_publicly_only_in_demo_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT", "4321")
    assert bind_address(demo_settings(tmp_path), None) == ("0.0.0.0", 4321)
    assert bind_address(Settings(_env_file=None), None) == ("127.0.0.1", 8000)
    monkeypatch.delenv("PORT")
    assert bind_address(demo_settings(tmp_path), None) == ("0.0.0.0", 8000)


@pytest.mark.parametrize("key", ["ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"])
def test_refuses_to_start_with_an_api_key(tmp_path, monkeypatch, key):
    monkeypatch.setenv(key, "sk-test-not-real")
    with pytest.raises(ValueError, match="runs without an API key"):
        demo_settings(tmp_path)


def test_locks_the_cached_configuration(tmp_path):
    s = demo_settings(tmp_path, llm_offline=False, llm_model="other", max_auto_amount=5,
                      review_new_suppliers=True)  # fmt: skip
    assert (s.llm_provider, s.llm_model, s.llm_offline) == (
        "openrouter",
        "anthropic/claude-haiku-5.5",
        True,
    )
    assert s.max_auto_amount is None and s.review_new_suppliers is False


def test_next_reset_is_the_next_full_hour():
    now = datetime(2026, 10, 16, 11, 59, 30, tzinfo=UTC)
    assert demo.next_reset(now) == datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


# --- pages -----------------------------------------------------------------------------


def test_start_page_and_banner(client):
    assert client.get("/").headers["location"] == "/demo"
    page = client.get("/demo").text
    assert BANNER in page
    assert page.count('action="/demo/process/') == len(SAMPLES) == 11
    for path in ("/review", "/demo/erp"):
        assert BANNER in client.get(path).text


def test_seed_is_processed_and_exported_at_startup(client, service, erp):
    (seeded,) = service.list()
    assert seeded.status is InvoiceStatus.EXPORTED
    assert seeded.invoice_number == SEED_NUMBER
    assert len(erp.invoices()) == 1
    assert SEED_NUMBER in client.get("/demo/erp").text


@pytest.mark.parametrize("sample", SAMPLES, ids=[s.invoice_id for s in SAMPLES])
def test_every_card_gives_its_expected_result(client, service, sample):
    record = service.get(pick(client, sample.invoice_id))
    assert record.status.value == sample.expected, record.reasons
    assert record.events[0].actor == "demo"
    assert not any("LLM extraction failed" in b for b in record.blocking)  # cache hit


def test_low_confidence_card_is_held_for_confidence_only(client, service):
    record = service.get(pick(client, "test-053"))
    assert record.held_for_confidence_only
    assert all(r.status.value != "fail" for r in record.rules)


def test_same_card_twice_is_a_duplicate(client, service):
    pick(client, "test-011")
    again = service.get(pick(client, "test-011"))
    assert any(r.startswith("R6 duplicate failed") for r in again.reasons)


# --- no free text ----------------------------------------------------------------------


def test_uploads_and_rest_changes_are_refused(client, service):
    files = {"file": ("x.xml", b"<x/>")}
    assert client.post("/invoices", files=files).status_code == 403
    invoice_id = pick(client, "test-053")
    for method, path, body in [
        ("post", f"/invoices/{invoice_id}/approve", {"reviewer": "x", "note": "anything"}),
        ("post", f"/invoices/{invoice_id}/reject", {"reviewer": "x", "reason": "anything"}),
        ("patch", f"/invoices/{invoice_id}/fields", {"reviewer": "x", "fields": {}}),
        ("post", f"/invoices/{invoice_id}/export", {"reviewer": "x"}),
    ]:
        assert getattr(client, method)(path, json=body).status_code == 403
    assert client.get(f"/invoices/{invoice_id}").status_code == 200  # reading works


def test_approve_with_a_preset_note_and_fixed_name(client, service):
    invoice_id = pick(client, "test-014")  # wrong gross: a rule failed, a note is needed
    page = client.get(f"/review/{invoice_id}").text
    assert 'name="reviewer"' not in page and "Acting as <strong>Demo visitor</strong>" in page
    assert '<select name="note">' in page
    free = client.post(f"/review/{invoice_id}/approve",
                       data={"note": "my own words", "reviewer": "Mallory"})  # fmt: skip
    assert free.status_code == 400 and "choose one of the given notes" in free.text
    ok = client.post(f"/review/{invoice_id}/approve",
                     data={"note": demo.APPROVE_NOTES[0], "reviewer": "Mallory"})  # fmt: skip
    assert ok.status_code == 303
    record = service.get(invoice_id)
    approval = next(e for e in record.events if e.new_status == "approved")
    assert (approval.actor, approval.note) == ("web:Demo visitor", demo.APPROVE_NOTES[0])
    assert record.status is InvoiceStatus.EXPORTED


def test_low_confidence_approves_without_a_note(client, service):
    invoice_id = pick(client, "test-053")
    assert client.post(f"/review/{invoice_id}/approve", data={"note": ""}).status_code == 303
    assert service.get(invoice_id).status is InvoiceStatus.EXPORTED


def test_reject_needs_a_preset_reason(client, service):
    invoice_id = pick(client, "test-061")
    bad = client.post(f"/review/{invoice_id}/reject", data={"reason": "rude text"})
    assert bad.status_code == 400
    ok = client.post(f"/review/{invoice_id}/reject", data={"reason": demo.REJECT_REASONS[2]})
    assert ok.status_code == 303
    assert service.get(invoice_id).events[-1].note == "Rejected: invalid bank details"


def test_amounts_can_be_corrected_but_text_cannot(client, service):
    invoice_id = pick(client, "test-014")
    record = service.get(invoice_id)
    fixed_gross = str(record.data.net_total + record.data.vat_total)
    form = {
        "gross_total": fixed_gross,
        "seller_name": "Offensive Text GmbH",  # a crafted request: must be ignored
        "line_count": str(len(record.data.line_items)),
        "line-0-description": "more text",
        "line-0-net_amount": str(record.data.line_items[0].net_amount),
    }
    response = client.post(f"/review/{invoice_id}/fields", data=form)
    assert response.status_code == 303
    after = service.get(invoice_id)
    assert str(after.data.gross_total) == fixed_gross
    assert after.data.seller_name == record.data.seller_name
    assert after.data.line_items[0].description == record.data.line_items[0].description
    assert after.corrected_by == {"gross_total": "web:Demo visitor"}
    assert not any(r.startswith("R2") for r in after.reasons)
    page = client.get(f"/review/{invoice_id}").text
    seller_input = page.split('id="f-seller_name"', 1)[1].split(">", 1)[0]
    assert "disabled" in seller_input


# --- reset, cap, self-check ------------------------------------------------------------


def test_reset_clears_everything_and_reseeds(client, service, erp, tmp_path):
    pick(client, "test-011")
    pick(client, "test-004")
    assert len(service.list()) == 3 and len(erp.invoices()) == 3
    demo.reset(service, lambda: erp.http.post("/api/reset"))
    (seeded,) = service.list()
    assert seeded.invoice_number == SEED_NUMBER
    assert [i["erp_id"] for i in erp.invoices()] == ["ERP-000001"]  # numbering restarts
    duplicate = service.get(pick(client, "test-046"))
    assert any(r.startswith("R6 duplicate failed") for r in duplicate.reasons)


def test_demo_full(client, monkeypatch):
    monkeypatch.setattr(demo, "MAX_INVOICES_PER_HOUR", 2)
    pick(client, "test-011")  # seed + 1 = 2
    full = client.post("/demo/process/test-001")
    assert full.status_code == 429
    assert "until the next reset" in full.text


def test_unknown_sample(client):
    assert client.post("/demo/process/test-999").status_code == 404


def test_cross_site_pick_is_refused(client):
    response = client.post("/demo/process/test-011", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_startup_fails_when_a_sample_misses_the_cache(service, erp, tmp_path):
    service.pipeline.llm.cache.directory = tmp_path / "empty-cache"
    app = create_app(service, demo_erp_reset=lambda: None)
    with pytest.raises(DemoSetupError, match="No cached LLM answer"), TestClient(app):
        pass


def test_demo_pages_do_not_exist_outside_demo_mode(tmp_path):
    from .services import make_service

    client = TestClient(create_app(make_service(tmp_path)), follow_redirects=False)
    assert client.get("/demo").status_code == 404
    assert client.get("/").headers["location"] == "/review"
    assert BANNER not in client.get("/review").text
