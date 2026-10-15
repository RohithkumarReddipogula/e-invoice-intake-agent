"""REST API (SPEC section 8.1). JSON in, JSON out; all logic lives in the service layer."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from .pipeline import UploadRejected
from .records import InvoiceRecord, InvoiceSummary
from .service import IntakeService, InvalidTransition, NotFound, ValidationProblem
from .status import InvoiceStatus

Reviewer = Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]


class CorrectRequest(BaseModel):
    reviewer: Reviewer
    fields: dict[str, Any]


class ApproveRequest(BaseModel):
    reviewer: Reviewer
    note: str | None = Field(default=None, max_length=2000)


class ExportRequest(BaseModel):
    reviewer: Reviewer


class RejectRequest(BaseModel):
    reviewer: Reviewer
    reason: str = Field(min_length=1, max_length=2000)


def invoice_json(record: InvoiceRecord) -> dict[str, Any]:
    return {
        **record.model_dump(mode="json"),
        "held_for_confidence_only": record.held_for_confidence_only,
        "erp_id": record.erp_id,
    }


def service_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, InvalidTransition):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValidationProblem | UploadRejected):
        return HTTPException(422, str(exc))
    raise exc


def build_router(service: IntakeService) -> APIRouter:
    router = APIRouter(prefix="/invoices", tags=["invoices"])

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (NotFound, InvalidTransition, ValidationProblem, UploadRejected) as exc:
            raise service_errors(exc) from exc

    @router.post("", status_code=201)
    async def upload(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        # Read one byte more than allowed, so too-large files are rejected without
        # reading them completely into memory.
        content = await file.read(service.settings.max_upload_bytes + 1)
        record = call(service.ingest, content, file.filename or "upload", actor="api")
        return invoice_json(record)

    @router.get("")
    def list_invoices(
        status: InvoiceStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[InvoiceSummary]:
        return service.list(status, limit)

    @router.get("/{invoice_id}")
    def get_invoice(invoice_id: str) -> dict[str, Any]:
        return invoice_json(call(service.get, invoice_id))

    @router.patch("/{invoice_id}/fields")
    def correct(invoice_id: str, body: CorrectRequest) -> dict[str, Any]:
        actor = f"api:{body.reviewer.strip()}"
        return invoice_json(call(service.correct_fields, invoice_id, body.fields, actor))

    @router.post("/{invoice_id}/approve")
    def approve(invoice_id: str, body: ApproveRequest) -> dict[str, Any]:
        actor = f"api:{body.reviewer.strip()}"
        return invoice_json(call(service.approve, invoice_id, actor, body.note))

    @router.post("/{invoice_id}/reject")
    def reject(invoice_id: str, body: RejectRequest) -> dict[str, Any]:
        actor = f"api:{body.reviewer.strip()}"
        return invoice_json(call(service.reject, invoice_id, actor, body.reason))

    @router.post("/{invoice_id}/export")
    def export(invoice_id: str, body: ExportRequest) -> dict[str, Any]:
        """Send an approved invoice to the ERP again, e.g. after export_failed."""
        actor = f"api:{body.reviewer.strip()}"
        return invoice_json(call(service.export, invoice_id, actor))

    @router.get("/{invoice_id}/file")
    def original_file(invoice_id: str) -> Response:
        record = call(service.get, invoice_id)
        content = service.pipeline.file_path(record.file_sha256).read_bytes()
        return file_response(record, content)

    return router


def file_response(record: InvoiceRecord, content: bytes) -> Response:
    """Serve the uploaded file without letting it run anything in our origin.

    XML goes out as text/plain: an XML document with XHTML in it would otherwise run
    scripts. `sandbox` blocks scripts in PDFs and anything else the browser renders.
    """
    is_pdf = record.input_kind != "xml"
    return Response(
        content,
        media_type="application/pdf" if is_pdf else "text/plain; charset=utf-8",
        headers={
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{record.id}.{"pdf" if is_pdf else "xml"}"',
        },
    )
