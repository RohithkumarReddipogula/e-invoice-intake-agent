"""Public demo mode (INTAKE_DEMO_MODE=1): sample invoices from the eval test split, cached
LLM and KoSIT results, no API key, no free text from visitors, hourly reset.
"""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from . import db
from .llm.extract import pdf_text
from .records import InvoiceRecord
from .service import IntakeService, ValidationProblem

DEMO_ACTOR = "web:Demo visitor"
MAX_INVOICES_PER_HOUR = 300
# The eval checked dates as of this day (eval/dataset.py EVAL_TODAY). The demo uses the same
# day, otherwise the samples drift: the "impossible date" card is only in the future then.
DEMO_TODAY = date(2026, 10, 15)
# A reset and a card being processed must not overlap (the reset deletes the stored files).
_LOCK = threading.Lock()

# Preset notes instead of free text, so no visitor's text is shown to the next visitor.
APPROVE_NOTES = (
    "Checked against the original",
    "Value corrected from the PDF",
    "Confirmed with the supplier by phone",
)
REJECT_REASONS = (
    "Rejected: wrong amount",
    "Rejected: duplicate invoice",
    "Rejected: invalid bank details",
)
# Fields a visitor may correct: amounts and dates only (no free text).
EDITABLE_FIELDS = frozenset(
    {"issue_date", "due_date", "allowance_total", "charge_total", "net_total", "vat_total",
     "gross_total"}
)  # fmt: skip
EDITABLE_LINE_KEYS = frozenset({"quantity", "unit_price", "net_amount", "vat_rate"})


@dataclass(frozen=True)
class Sample:
    invoice_id: str  # id in the eval test split
    title: str
    story: str
    expected: str  # expected status after processing


SAMPLES = (
    Sample("test-011", "Clean XRechnung (UBL)",
           "Read directly from the XML, validated by KoSIT, all checks pass.", "exported"),
    Sample("test-001", "Clean XRechnung (CII)",
           "The other XRechnung syntax; same path, no LLM involved.", "exported"),
    Sample("test-002", "Clean ZUGFeRD PDF",
           "A PDF with the invoice XML embedded; the XML is read, not the picture.", "exported"),
    Sample("test-004", "Clean plain PDF, German",
           "No XML: the LLM extracts the fields, every value is found in the PDF text.",
           "exported"),
    Sample("test-005", "Clean plain PDF, English",
           "English layout with dates like '15 September 2026', same result.", "exported"),
    Sample("test-056", "Wrong total in XML",
           "Net total does not match the lines: rule R1 fails and KoSIT rejects it (BR-CO-13).",
           "needs_review"),
    Sample("test-014", "Wrong gross on a plain PDF",
           "Net + VAT is not the printed gross total: rule R2 sends it to a person.",
           "needs_review"),
    Sample("test-061", "Bad IBAN",
           "KoSIT accepts the XML, but our IBAN checksum rule R4 catches the broken IBAN.",
           "needs_review"),
    Sample("test-046", "Duplicate",
           "The same invoice as one that arrived earlier as XRechnung, now as a PDF: R6.",
           "needs_review"),
    Sample("test-053", "Low confidence",
           "All checks pass, but one field is below the confidence threshold: a person "
           "confirms it, no note needed.", "needs_review"),
    Sample("test-074", "Impossible date",
           "The issue date lies in the future: rule R5.", "needs_review"),
)  # fmt: skip

# Processed at every reset, so the duplicate card has an earlier invoice to collide with.
SEED = ("test-026",)


class DemoSetupError(Exception):
    """The demo cannot start: a sample or one of its cache entries is missing."""


class DemoFull(Exception):
    pass


def sample(sample_id: str) -> Sample:
    for s in SAMPLES:
        if s.invoice_id == sample_id:
            return s
    raise KeyError(sample_id)


def _manifest(data_dir: Path) -> dict[str, dict]:
    entries = json.loads((data_dir / "manifest.json").read_text())["invoices"]
    return {m["id"]: m for m in entries}


def sample_file(service: IntakeService, invoice_id: str) -> Path:
    data_dir = service.settings.demo_data_dir
    return data_dir / _manifest(data_dir)[invoice_id]["file"]


def self_check(service: IntakeService, kosit_cache) -> None:
    """Every sample and seed must hit the committed caches, or the demo would silently send
    invoices to review for the wrong reason (e.g. another pypdf version, other cache keys)."""
    manifest = _manifest(service.settings.demo_data_dir)
    llm = service.pipeline.llm
    for invoice_id in (*SEED, *(s.invoice_id for s in SAMPLES)):
        entry = manifest.get(invoice_id)
        if entry is None:
            raise DemoSetupError(f"{invoice_id} is not in {service.settings.demo_data_dir}.")
        path = service.settings.demo_data_dir / entry["file"]
        if entry["kind"] == "plain_pdf":
            if llm.cache.get(llm.key_for(pdf_text(path.read_bytes()))) is None:
                raise DemoSetupError(
                    f"No cached LLM answer for {invoice_id}: the image extracts other PDF text "
                    "than the eval run (pypdf version?)."
                )
        elif not kosit_cache.path_for(path).exists():
            raise DemoSetupError(f"No cached KoSIT result for {invoice_id}.")


def next_reset(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def reset(service: IntakeService, erp_reset: Callable[[], None]) -> None:
    """Wipe invoices, stored files and the mock ERP, then process the seed again."""
    with _LOCK:
        with service.database.connect() as conn:  # one transaction
            for table in ("events", "exports", "rule_results", "fields", "duplicate_keys",
                          "invoices"):  # fmt: skip
                conn.execute(f"DELETE FROM {table}")
        files = service.settings.files_dir
        if files.exists():
            shutil.rmtree(files)
        erp_reset()
        for invoice_id in SEED:
            path = sample_file(service, invoice_id)
            service.ingest(path.read_bytes(), path.name, actor="demo:seed")


def http_erp_reset(service: IntakeService) -> Callable[[], None]:
    def call() -> None:
        if service.settings.erp_base_url:
            httpx.post(f"{service.settings.erp_base_url}/api/reset", timeout=5.0)

    return call


def process(service: IntakeService, sample_id: str) -> InvoiceRecord:
    """A visitor picked a card: run that file through the normal pipeline."""
    s = sample(sample_id)
    with _LOCK:
        with service.database.connect() as conn:
            count = db.count_invoices(conn)
        if count >= MAX_INVOICES_PER_HOUR:
            raise DemoFull(
                f"The demo holds {MAX_INVOICES_PER_HOUR} invoices until the next reset. "
                "Please come back after the full hour."
            )
        path = sample_file(service, s.invoice_id)
        return service.ingest(path.read_bytes(), path.name, actor="demo")


def check_note(note: str, allowed: tuple[str, ...]) -> str:
    if note not in allowed:
        raise ValidationProblem("Please choose one of the given notes.")
    return note
