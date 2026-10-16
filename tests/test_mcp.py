"""MCP tools through a real MCP client: in-process, and once over stdio."""

import asyncio
import json
import sys

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import Implementation

from intake.mcp_server import actor_for, build_server
from intake.status import InvoiceStatus

from .conftest import render_pdf
from .erp_fakes import Erp
from .llm_fakes import INVOICE_LINES, answer
from .services import make_service

WRONG_NET = (b"<ram:TaxBasisTotalAmount>230.00", b"<ram:TaxBasisTotalAmount>240.00")
# The plain-PDF fixture is invoice RE-2026-0042 of the UBL seller, so an auto-approved
# UBL upload in the same test needs another number (else R6 flags it as a duplicate).
OTHER_NUMBER = (b"<cbc:ID>RE-2026-0042</cbc:ID>", b"<cbc:ID>RE-2026-0043</cbc:ID>")
LOW_BUYER = answer(buyer_name={"value": "Beispiel Handels AG", "confidence": 0.5})


def call(service, tool, args=None, client_name="claude-desktop"):
    """Call one tool; returns (is_error, structured result or error text)."""

    async def run():
        info = Implementation(name=client_name, version="1.0")
        async with Client(build_server(service), client_info=info) as client:
            result = await client.call_tool(tool, args or {})
        if result.is_error:
            return True, result.content[0].text
        data = result.structured_content
        return False, data.get("result", data)

    return asyncio.run(run())


@pytest.fixture
def service(tmp_path):
    return make_service(tmp_path, llm_output=LOW_BUYER)


@pytest.fixture
def low_confidence(service):
    """In review only because buyer_name has low confidence."""
    record = service.ingest(render_pdf(INVOICE_LINES), "x.pdf")
    assert record.held_for_confidence_only
    return record


@pytest.fixture
def rule_failure(service, cii_xml):
    record = service.ingest(cii_xml.replace(*WRONG_NET), "x.xml")
    assert not record.held_for_confidence_only
    return record


def test_exactly_three_tools(service):
    async def run():
        async with Client(build_server(service)) as client:
            return (await client.list_tools()).tools

    tools = {t.name: t for t in asyncio.run(run())}
    assert set(tools) == {"list_invoices", "get_invoice", "approve_invoice"}
    assert tools["list_invoices"].annotations.read_only_hint is True
    assert tools["get_invoice"].annotations.read_only_hint is True
    assert tools["approve_invoice"].annotations.read_only_hint is False
    assert "untrusted" in tools["get_invoice"].description
    assert set(tools["approve_invoice"].input_schema["required"]) == {"invoice_id", "note"}


# --- read tools ------------------------------------------------------------------------


def test_list_invoices(service, low_confidence, rule_failure, ubl_xml):
    auto = service.ingest(ubl_xml.replace(*OTHER_NUMBER), "auto.xml")
    assert auto.status is InvoiceStatus.AUTO_APPROVED
    error, everything = call(service, "list_invoices")
    assert not error
    assert len(everything) == 3
    error, review = call(service, "list_invoices", {"status": "needs_review", "limit": 5})
    assert {i["id"] for i in review} == {low_confidence.id, rule_failure.id}
    assert all(i["main_reason"] for i in review)


@pytest.mark.parametrize("args", [{"limit": 0}, {"limit": 101}, {"status": "nonsense"}])
def test_list_invoices_invalid_arguments(service, args):
    error, _ = call(service, "list_invoices", args)
    assert error


def test_get_invoice(service, low_confidence):
    error, invoice = call(service, "get_invoice", {"invoice_id": low_confidence.id})
    assert not error
    assert invoice["status"] == "needs_review"
    assert invoice["held_for_confidence_only"] is True
    assert invoice["confidence"]["buyer_name"] == 0.5
    assert invoice["data"]["gross_total"] == "2082.50"
    assert invoice["reasons"] == [
        "Field `buyer_name` has low confidence (0.50): below the threshold."
    ]
    assert [r["rule_id"] for r in invoice["rules"]][-1] == "R7"


def test_get_unknown_invoice(service):
    error, text = call(service, "get_invoice", {"invoice_id": "nope"})
    assert error
    assert "No invoice with id 'nope'" in text


# --- approve_invoice -------------------------------------------------------------------


def test_approve_low_confidence_invoice(service, low_confidence):
    error, invoice = call(
        service,
        "approve_invoice",
        {"invoice_id": low_confidence.id, "note": "Buyer name checked against the PDF."},
    )
    assert not error, invoice
    assert invoice["status"] == "approved"
    last = service.get(low_confidence.id).events[-1]
    assert (last.actor, last.new_status, last.note) == (
        "mcp:claude-desktop",
        "approved",
        "Buyer name checked against the PDF.",
    )


def test_approve_refuses_rule_failures(service, rule_failure):
    error, text = call(
        service, "approve_invoice", {"invoice_id": rule_failure.id, "note": "looks fine to me"}
    )
    assert error
    assert "failed checks (R1, R2)" in text
    assert "review page" in text
    assert service.get(rule_failure.id).status is InvoiceStatus.NEEDS_REVIEW


def test_approve_refuses_blocking_problems(service):
    scan = service.ingest(render_pdf([]), "scan.pdf")
    error, text = call(service, "approve_invoice", {"invoice_id": scan.id, "note": "ok"})
    assert error
    assert "no invoice data" in text  # a scan without data: refused before anything else
    assert service.get(scan.id).status is InvoiceStatus.NEEDS_REVIEW


def test_approve_refuses_credit_notes(service, cii_xml):
    credit = cii_xml.replace(
        b"<ram:TypeCode>380</ram:TypeCode>", b"<ram:TypeCode>381</ram:TypeCode>"
    )
    record = service.ingest(credit, "credit.xml")
    error, text = call(service, "approve_invoice", {"invoice_id": record.id, "note": "ok"})
    assert error
    assert "blocking problem" in text
    assert service.get(record.id).status is InvoiceStatus.NEEDS_REVIEW


def test_approve_refuses_other_statuses(service, ubl_xml, low_confidence):
    auto = service.ingest(ubl_xml.replace(*OTHER_NUMBER), "auto.xml")
    assert auto.status is InvoiceStatus.AUTO_APPROVED
    error, text = call(service, "approve_invoice", {"invoice_id": auto.id, "note": "x"})
    assert error and "Invoice is auto_approved" in text
    call(service, "approve_invoice", {"invoice_id": low_confidence.id, "note": "checked"})
    args = {"invoice_id": low_confidence.id, "note": "again"}
    error, text = call(service, "approve_invoice", args)
    assert error and "Invoice is approved" in text


@pytest.mark.parametrize("note", ["", "   "])
def test_approve_needs_a_note(service, low_confidence, note):
    error, _ = call(service, "approve_invoice", {"invoice_id": low_confidence.id, "note": note})
    assert error
    assert service.get(low_confidence.id).status is InvoiceStatus.NEEDS_REVIEW


def test_approve_exports_to_the_erp(tmp_path):
    erp = Erp(tmp_path)
    service = make_service(tmp_path, llm_output=LOW_BUYER, erp=erp.client())
    record = service.ingest(render_pdf(INVOICE_LINES), "x.pdf")
    error, invoice = call(service, "approve_invoice", {"invoice_id": record.id, "note": "checked"})
    assert not error
    assert invoice["status"] == "exported"
    assert erp.invoices()[0]["invoice"]["approval"]["by"] == "mcp:claude-desktop"


@pytest.mark.parametrize(
    ("name", "actor"),
    [
        ("claude-desktop", "mcp:claude-desktop"),
        ("Claude Code 2.1", "mcp:Claude_Code_2.1"),
        ("evil\nname; drop table", "mcp:evil_name_drop_table"),
        ("", "mcp:unknown"),
        ("x" * 200, "mcp:" + "x" * 64),
    ],
)
def test_client_name_in_the_audit_trail(service, low_confidence, name, actor):
    call(service, "approve_invoice", {"invoice_id": low_confidence.id, "note": "ok"}, name)
    assert service.get(low_confidence.id).events[-1].actor == actor


def test_actor_without_context():
    assert actor_for(None) == "mcp:unknown"


# --- the real entry point over stdio ---------------------------------------------------


def test_stdio_entry_point(tmp_path, ubl_xml):
    service = make_service(tmp_path)
    record = service.ingest(ubl_xml, "x.xml")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "intake.mcp_server"],
        cwd=str(tmp_path),  # no .env here
        env={
            "INTAKE_DATABASE_PATH": str(tmp_path / "intake.db"),
            "INTAKE_FILES_DIR": str(tmp_path / "files"),
            "INTAKE_LLM_OFFLINE": "1",
            "INTAKE_ERP_BASE_URL": "",
        },
    )

    async def run():
        async with Client(params) as client:
            tools = (await client.list_tools()).tools
            result = await client.call_tool("get_invoice", {"invoice_id": record.id})
            return tools, result

    tools, result = asyncio.run(run())
    assert {t.name for t in tools} == {"list_invoices", "get_invoice", "approve_invoice"}
    assert not result.is_error
    payload = result.structured_content or json.loads(result.content[0].text)
    assert payload["data"]["invoice_number"] == "RE-2026-0042"
