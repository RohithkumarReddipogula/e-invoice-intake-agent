"""The service layer: one place with the logic that REST, the web page and MCP share."""

from __future__ import annotations

from dataclasses import dataclass

from . import db
from .config import Settings
from .llm import LlmExtractor
from .pipeline import Pipeline
from .records import InvoiceRecord, InvoiceSummary
from .status import InvoiceStatus

MAX_LIST_LIMIT = 100


class NotFound(Exception):
    pass


@dataclass
class IntakeService:
    pipeline: Pipeline

    @classmethod
    def from_settings(cls, settings: Settings) -> IntakeService:
        database = db.Database(settings.database_path)
        return cls(Pipeline(settings, database, LlmExtractor(settings)))

    @property
    def database(self) -> db.Database:
        return self.pipeline.database

    def ingest(self, content: bytes, filename: str, actor: str = "system") -> InvoiceRecord:
        return self.pipeline.ingest(content, filename, actor)

    def get(self, invoice_id: str) -> InvoiceRecord:
        with self.database.connect() as conn:
            record = db.get_invoice(conn, invoice_id)
        if record is None:
            raise NotFound(f"No invoice with id {invoice_id!r}.")
        return record

    def list(self, status: InvoiceStatus | None = None, limit: int = 20) -> list[InvoiceSummary]:
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        with self.database.connect() as conn:
            return db.list_invoices(conn, status, limit)
