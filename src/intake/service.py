"""The service layer: one place with the logic that REST, the web page and MCP share."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from . import db
from .config import Settings
from .decision import decide
from .erp_client import ErpClient, ErpError
from .llm import LlmExtractor
from .models import FIELD_NAMES, Extraction, InvoiceData, Source
from .pipeline import Pipeline
from .records import InvoiceRecord, InvoiceSummary
from .rules import duplicate_keys, run_rules
from .status import InvoiceStatus

MAX_LIST_LIMIT = 100
EXPORTABLE = {InvoiceStatus.AUTO_APPROVED, InvoiceStatus.APPROVED, InvoiceStatus.EXPORT_FAILED}
APPROVED_STATUSES = {InvoiceStatus.AUTO_APPROVED, InvoiceStatus.APPROVED}


class NotFound(Exception):
    pass


class InvalidTransition(Exception):
    """The invoice is not in a status that allows this action."""


class ValidationProblem(Exception):
    """The request is understood but not acceptable, e.g. a missing note."""


@dataclass
class IntakeService:
    pipeline: Pipeline
    erp: ErpClient | None = None  # None: no export, approved invoices stay approved

    @classmethod
    def from_settings(cls, settings: Settings) -> IntakeService:
        database = db.Database(settings.database_path)
        erp = ErpClient(settings.erp_base_url) if settings.erp_base_url else None
        return cls(Pipeline(settings, database, LlmExtractor(settings)), erp)

    @property
    def database(self) -> db.Database:
        return self.pipeline.database

    @property
    def settings(self) -> Settings:
        return self.pipeline.settings

    # --- reads ---------------------------------------------------------------------------

    def ingest(self, content: bytes, filename: str, actor: str = "system") -> InvoiceRecord:
        record = self.pipeline.ingest(content, filename, actor)
        if record.status is InvoiceStatus.AUTO_APPROVED and self.erp is not None:
            record = self.export(record.id, actor="system")
        return record

    def get(self, invoice_id: str) -> InvoiceRecord:
        with self.database.connect() as conn:
            return _get(conn, invoice_id)

    def list(self, status: InvoiceStatus | None = None, limit: int = 20) -> list[InvoiceSummary]:
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        with self.database.connect() as conn:
            return db.list_invoices(conn, status, limit)

    # --- review actions ------------------------------------------------------------------

    def correct_fields(self, invoice_id: str, changes: dict[str, Any], actor: str) -> InvoiceRecord:
        """A person corrects field values. Changed fields become fully confident, R1-R6 run
        again and the review reasons are updated. The invoice stays in review: approving is
        a separate, explicit step."""
        unknown = sorted(set(changes) - set(FIELD_NAMES))
        if unknown:
            raise ValidationProblem(f"Unknown field(s): {', '.join(unknown)}.")
        with self.database.connect() as conn:
            record = _get(conn, invoice_id)
            _require_status(record, InvoiceStatus.NEEDS_REVIEW, "corrected")
            before = record.data or InvoiceData()
            try:
                data = InvoiceData.model_validate({**before.model_dump(), **changes})
            except ValidationError as exc:
                raise ValidationProblem(_validation_message(exc)) from exc

            changed = [
                n for n in FIELD_NAMES if n in changes and getattr(data, n) != getattr(before, n)
            ]
            if not changed:
                return record

            confidence = {n: record.confidence.get(n, 0.0) for n in FIELD_NAMES}
            notes = {n: note for n, note in record.notes.items() if n not in changed}
            corrected_by = dict(record.corrected_by)
            for name in changed:
                confidence[name] = 1.0
                corrected_by[name] = actor
            for name in FIELD_NAMES:
                if record.data is None and name not in changed:
                    notes.setdefault(name, "not extracted; enter the value")
            source = Source(record.source) if record.source else Source.MANUAL
            extraction = Extraction(source=source, data=data, confidence=confidence, notes=notes)
            db.save_extraction(conn, invoice_id, extraction, corrected_by)

            # R7 checked the original file, which a correction does not change: keep it.
            structured = [r for r in record.rules if r.rule_id == "R7"]
            existing = db.existing_duplicate_keys(conn, exclude_id=invoice_id)
            rules = run_rules(data, today=self.pipeline.today(), existing=existing) + structured
            db.save_rules(conn, invoice_id, rules)
            db.save_duplicate_keys(conn, invoice_id, duplicate_keys(data))

            decision = decide(
                extraction, rules, self.settings.confidence_threshold, record.blocking
            )
            db.set_status(
                conn,
                invoice_id,
                InvoiceStatus.NEEDS_REVIEW,
                actor=actor,
                note=f"corrected: {', '.join(changed)}",
                reasons=decision.reasons,
            )
            return _get(conn, invoice_id)

    def approve(self, invoice_id: str, actor: str, note: str | None = None) -> InvoiceRecord:
        note = (note or "").strip() or None
        with self.database.connect() as conn:
            record = _get(conn, invoice_id)
            _require_status(record, InvoiceStatus.NEEDS_REVIEW, "approved")
            if record.data is None:
                raise ValidationProblem("There is no invoice data yet. Enter the fields first.")
            problems = [r.rule_id for r in record.failed_rules] + (
                ["blocking problem"] if record.blocking else []
            )
            if problems and note is None:
                raise ValidationProblem(
                    "A note is required to approve an invoice with failed checks "
                    f"({', '.join(problems)})."
                )
            db.set_status(conn, invoice_id, InvoiceStatus.APPROVED, actor=actor, note=note)
            record = _get(conn, invoice_id)
        if self.erp is not None:
            record = self.export(invoice_id, actor=actor)
        return record

    def export(self, invoice_id: str, actor: str) -> InvoiceRecord:
        """Send an approved invoice to the ERP. An ERP failure is not an exception: the
        invoice goes to export_failed with the error and can be exported again later."""
        if self.erp is None:
            raise ValidationProblem("No ERP is configured (INTAKE_ERP_BASE_URL is empty).")
        record = self.get(invoice_id)
        if record.status not in EXPORTABLE:
            allowed = ", ".join(sorted(s.value for s in EXPORTABLE))
            raise InvalidTransition(
                f"Invoice is {record.status.value}; only {allowed} invoices can be exported."
            )

        # The HTTP calls (with retries and backoff) run outside the database transaction.
        try:
            result = self.erp.send(invoice_id, erp_payload(record))
        except ErpError as exc:
            result, error = None, exc

        with self.database.connect() as conn:
            current = _get(conn, invoice_id)
            if result is not None:
                db.add_export(
                    conn, invoice_id, actor=actor, ok=True, attempts=result.attempts,
                    erp_id=result.erp_id,
                )  # fmt: skip
                if current.status is not InvoiceStatus.EXPORTED:
                    note = f"ERP id {result.erp_id}"
                    if not result.created:
                        note += " (the ERP already had it)"
                    db.set_status(conn, invoice_id, InvoiceStatus.EXPORTED, actor=actor, note=note)
            else:
                db.add_export(
                    conn, invoice_id, actor=actor, ok=False, attempts=error.attempts,
                    error=str(error),
                )  # fmt: skip
                # Another export may have succeeded in the meantime; never undo that.
                if current.status is not InvoiceStatus.EXPORTED:
                    db.set_status(
                        conn, invoice_id, InvoiceStatus.EXPORT_FAILED, actor=actor, note=str(error)
                    )
            return _get(conn, invoice_id)

    def reject(self, invoice_id: str, actor: str, reason: str) -> InvoiceRecord:
        reason = reason.strip()
        if not reason:
            raise ValidationProblem("A reason is required to reject an invoice.")
        with self.database.connect() as conn:
            record = _get(conn, invoice_id)
            _require_status(record, InvoiceStatus.NEEDS_REVIEW, "rejected")
            db.set_status(conn, invoice_id, InvoiceStatus.REJECTED, actor=actor, note=reason)
            return _get(conn, invoice_id)


def erp_payload(record: InvoiceRecord) -> dict[str, Any]:
    """What the ERP receives. Built only from data that no longer changes after approval,
    so a retry sends exactly the same body (the mock ERP checks that)."""
    approval = next(e for e in reversed(record.events) if e.new_status in APPROVED_STATUSES)
    assert record.data is not None
    return {
        "invoice_id": record.id,
        "source": record.source,
        "approval": {
            "status": approval.new_status,
            "by": approval.actor,
            "at": approval.at,
            "note": approval.note,
        },
        "invoice": record.data.model_dump(mode="json"),
    }


def _get(conn, invoice_id: str) -> InvoiceRecord:
    record = db.get_invoice(conn, invoice_id)
    if record is None:
        raise NotFound(f"No invoice with id {invoice_id!r}.")
    return record


def _require_status(record: InvoiceRecord, status: InvoiceStatus, action: str) -> None:
    if record.status is not status:
        raise InvalidTransition(
            f"Invoice is {record.status.value}; only invoices in {status.value} can be {action}."
        )


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error["loc"])
        parts.append(f"{where}: {error['msg']}")
    return "Invalid value(s): " + "; ".join(parts)
