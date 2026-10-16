"""Review web page (SPEC section 8.2): a queue and a detail page with correct/approve/reject.

Server-rendered with Jinja2 (autoescaped), no JavaScript. Every action goes through the
service layer, same as the REST API.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import demo as demo_mode
from .models import FIELD_NAMES
from .service import EXPORTABLE, IntakeService, InvalidTransition, NotFound, ValidationProblem
from .status import InvoiceStatus

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
MAX_XML_PREVIEW = 200_000  # characters
EMPTY_LINE_ROWS = 2  # blank rows under the line items, for adding lines
LINE_KEYS = ("description", "quantity", "unit_price", "net_amount", "vat_rate")

LABELS = {
    "invoice_number": "Invoice number",
    "issue_date": "Issue date",
    "due_date": "Due date",
    "currency": "Currency",
    "seller_name": "Seller",
    "seller_vat_id": "Seller VAT ID",
    "seller_iban": "Seller IBAN",
    "buyer_name": "Buyer",
    "line_items": "Line items",
    "allowance_total": "Allowances",
    "charge_total": "Charges",
    "net_total": "Net total",
    "vat_total": "VAT total",
    "gross_total": "Gross total",
}
DATE_FIELDS = {"issue_date", "due_date"}
ZERO_DEFAULT_FIELDS = {"allowance_total", "charge_total"}

DONE_MESSAGES = {
    "corrected": "Corrections saved. Checks ran again.",
    "unchanged": "Nothing changed.",
    "approved": "Invoice approved.",
    "rejected": "Invoice rejected.",
    "export": "Export to the ERP ran; see the status and the ERP section below.",
}


def local_time(iso: str, zone: ZoneInfo) -> str:
    """'2026-10-15T18:14:02+00:00' -> '2026-10-15 20:14 CEST' (stored times are UTC)."""
    return datetime.fromisoformat(iso).astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")


def build_web_router(service: IntakeService) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    zone = ZoneInfo(service.settings.display_timezone)
    TEMPLATES.env.filters["local_time"] = lambda iso: local_time(iso, zone)
    demo = service.settings.demo_mode

    def page(request: Request, name: str, context: dict[str, Any], status: int = 200):
        reviewer = "Demo visitor" if demo else request.cookies.get("reviewer", "")
        context = {"reviewer": reviewer, "demo": demo, **context}
        if demo:
            context["next_reset"] = local_time(demo_mode.next_reset().isoformat(), zone)
        return TEMPLATES.TemplateResponse(request, name, context, status_code=status)

    def reviewer_of(form) -> str | None:
        # Demo: no free text from visitors, the name is fixed.
        return "Demo visitor" if demo else _reviewer(form)

    def detail(
        request: Request,
        invoice_id: str,
        error: str | None = None,
        status: int = 200,
        entered=None,
    ):
        """`entered`: the submitted form, shown again after an invalid correction."""
        try:
            record = service.get(invoice_id)
        except NotFound:
            return page(request, "not_found.html", {"invoice_id": invoice_id}, 404)
        xml_text = None
        if record.input_kind == "xml":
            raw = service.pipeline.file_path(record.file_sha256).read_bytes()
            xml_text = raw.decode("utf-8", errors="replace")[:MAX_XML_PREVIEW]
        threshold = service.settings.confidence_threshold
        editable = record.status is InvoiceStatus.NEEDS_REVIEW
        if entered is not None:
            field_values, line_rows = entered["fields"], entered["lines"]
        else:
            field_values, line_rows = _stored_form_values(record, editable and not demo)
        context = {
            "record": record,
            "labels": LABELS,
            "scalar_fields": [n for n in FIELD_NAMES if n != "line_items"],
            "date_fields": DATE_FIELDS,
            "low": {n for n in FIELD_NAMES if record.confidence.get(n, 0.0) < threshold},
            "threshold": threshold,
            "xml_text": xml_text,
            "field_values": field_values,
            "line_rows": line_rows,
            "line_keys": LINE_KEYS,
            "editable": editable,
            "can_export": service.erp is not None and record.status in EXPORTABLE,
            "message": DONE_MESSAGES.get(request.query_params.get("done", "")),
            "error": error,
            # Demo: preset notes, and only amounts and dates can be corrected.
            "approve_notes": demo_mode.APPROVE_NOTES,
            "reject_reasons": demo_mode.REJECT_REASONS,
            "editable_fields": demo_mode.EDITABLE_FIELDS if demo else None,
            "editable_line_keys": demo_mode.EDITABLE_LINE_KEYS if demo else None,
        }
        if entered is not None and entered["reviewer"]:
            context["reviewer"] = entered["reviewer"]
        return page(request, "detail.html", context, status)

    def done(invoice_id: str, what: str, reviewer: str) -> RedirectResponse:
        response = RedirectResponse(f"/review/{invoice_id}?done={what}", status_code=303)
        if not demo:
            response.set_cookie("reviewer", reviewer, max_age=30 * 24 * 3600, samesite="strict")
        return response

    @router.get("/review", response_class=HTMLResponse)
    def queue(request: Request):
        return page(
            request,
            "queue.html",
            {
                "queue": service.list(InvoiceStatus.NEEDS_REVIEW, limit=100),
                "recent": service.list(limit=20),
            },
        )

    @router.get("/review/{invoice_id}", response_class=HTMLResponse)
    def show(request: Request, invoice_id: str):
        return detail(request, invoice_id)

    @router.post("/review/{invoice_id}/fields", response_class=HTMLResponse)
    async def correct(request: Request, invoice_id: str):
        form = await request.form()
        reviewer = reviewer_of(form)
        entered = _entered_form(form)
        if reviewer is None:
            return detail(request, invoice_id, "Enter your name before saving.", 400, entered)
        changes = _changes_from_form(form)
        try:
            before = service.get(invoice_id)
            if demo:
                changes = _demo_changes(changes, before, form)
            after = service.correct_fields(invoice_id, changes, f"web:{reviewer}")
        except NotFound:
            return detail(request, invoice_id)
        except ValidationProblem as exc:
            # Show the form again with what the reviewer typed, not the stored values.
            return detail(request, invoice_id, str(exc), 400, entered)
        except InvalidTransition as exc:
            return detail(request, invoice_id, str(exc), 400)
        return done(
            invoice_id, "corrected" if after.events != before.events else "unchanged", reviewer
        )

    @router.post("/review/{invoice_id}/approve", response_class=HTMLResponse)
    async def approve(request: Request, invoice_id: str):
        form = await request.form()
        reviewer = reviewer_of(form)
        if reviewer is None:
            return detail(request, invoice_id, "Enter your name before approving.", 400)
        note = str(form.get("note", ""))
        try:
            if demo and note:
                demo_mode.check_note(note, demo_mode.APPROVE_NOTES)
            service.approve(invoice_id, f"web:{reviewer}", note)
        except NotFound:
            return detail(request, invoice_id)
        except (ValidationProblem, InvalidTransition) as exc:
            return detail(request, invoice_id, str(exc), 400)
        return done(invoice_id, "approved", reviewer)

    @router.post("/review/{invoice_id}/export", response_class=HTMLResponse)
    async def export(request: Request, invoice_id: str):
        form = await request.form()
        reviewer = reviewer_of(form)
        if reviewer is None:
            return detail(request, invoice_id, "Enter your name before exporting.", 400)
        try:
            service.export(invoice_id, f"web:{reviewer}")
        except NotFound:
            return detail(request, invoice_id)
        except (ValidationProblem, InvalidTransition) as exc:
            return detail(request, invoice_id, str(exc), 400)
        return done(invoice_id, "export", reviewer)

    @router.post("/review/{invoice_id}/reject", response_class=HTMLResponse)
    async def reject(request: Request, invoice_id: str):
        form = await request.form()
        reviewer = reviewer_of(form)
        if reviewer is None:
            return detail(request, invoice_id, "Enter your name before rejecting.", 400)
        reason = str(form.get("reason", ""))
        try:
            if demo:
                demo_mode.check_note(reason, demo_mode.REJECT_REASONS)
            service.reject(invoice_id, f"web:{reviewer}", reason)
        except NotFound:
            return detail(request, invoice_id)
        except (ValidationProblem, InvalidTransition) as exc:
            return detail(request, invoice_id, str(exc), 400)
        return done(invoice_id, "rejected", reviewer)

    return router


def _reviewer(form) -> str | None:
    name = " ".join(str(form.get("reviewer", "")).split())[:100]
    return name or None


def _str(value: object) -> str:
    return "" if value is None else str(value)


def _stored_form_values(record, editable: bool) -> tuple[dict[str, str], list[dict[str, str]]]:
    """The stored invoice as form strings, plus empty rows for adding lines."""
    data = record.data
    fields = {n: _str(getattr(data, n)) if data else "" for n in FIELD_NAMES if n != "line_items"}
    rows = [
        {k: _str(getattr(line, k)) for k in LINE_KEYS} for line in (data.line_items if data else [])
    ]
    if editable:
        rows += [dict.fromkeys(LINE_KEYS, "") for _ in range(EMPTY_LINE_ROWS)]
    return fields, rows


def _entered_form(form) -> dict[str, Any]:
    """Exactly what was submitted, as strings, to put back into the form."""
    count = _line_count(form)
    return {
        "reviewer": " ".join(str(form.get("reviewer", "")).split())[:100],
        "fields": {n: str(form.get(n, "")) for n in FIELD_NAMES if n != "line_items"},
        "lines": [{k: str(form.get(f"line-{i}-{k}", "")) for k in LINE_KEYS} for i in range(count)],
    }


def _line_count(form) -> int:
    try:
        return max(0, min(int(str(form.get("line_count", "0"))), 1000))
    except ValueError:
        return 0


def _demo_changes(changes: dict[str, Any], record, form) -> dict[str, Any]:
    """Demo: keep only amount and date fields; line items keep their stored descriptions and
    count, only their numbers are read from the form (by row index). Enforced here, not just
    in the page."""
    kept = {k: v for k, v in changes.items() if k in demo_mode.EDITABLE_FIELDS}
    if "line_count" in form and record.data is not None:
        lines = []
        for i, stored in enumerate(record.data.line_items):
            row = {k: _str(getattr(stored, k)) or None for k in LINE_KEYS}
            for key in demo_mode.EDITABLE_LINE_KEYS:
                field = f"line-{i}-{key}"
                if field in form:
                    row[key] = str(form[field]).strip() or None
            lines.append(row)
        kept["line_items"] = lines
    return kept


def _changes_from_form(form) -> dict[str, Any]:
    """All submitted values; the service marks only the ones that differ as corrected."""
    changes: dict[str, Any] = {}
    for name in FIELD_NAMES:
        if name == "line_items" or name not in form:
            continue
        value = str(form[name]).strip()
        if value == "":
            changes[name] = "0" if name in ZERO_DEFAULT_FIELDS else None
        else:
            changes[name] = value

    if "line_count" in form:
        lines = []
        for i in range(_line_count(form)):
            row = {k: str(form.get(f"line-{i}-{k}", "")).strip() for k in LINE_KEYS}
            if not any(row.values()):
                continue  # an empty row: not a line
            lines.append({k: (v or None) for k, v in row.items()})
        changes["line_items"] = lines
    return changes
