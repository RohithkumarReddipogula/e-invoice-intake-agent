"""Upload -> detect -> extract -> rules -> decision -> stored invoice (SPEC sections 2-7)."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid4

from . import db
from .config import Settings
from .decision import decide
from .detect import Detected, InputKind, UnsupportedDocument, detect
from .guardrails import guardrail_reasons
from .llm import LlmError, LlmExtractor, NoTextLayer, extract_llm
from .models import Extraction
from .records import InvoiceRecord
from .rules import duplicate_keys, run_rules
from .rules.duplicate import seller_keys
from .status import InvoiceStatus
from .structured import StructuredValidator, check_structured, kosit_validator
from .xml_extract import extract_xml


class UploadRejected(Exception):
    """Not an invoice we can process; nothing is stored."""


@dataclass
class Pipeline:
    settings: Settings
    database: db.Database
    llm: LlmExtractor
    validator: StructuredValidator = kosit_validator
    today: Callable[[], date] = field(default=date.today)

    def ingest(self, content: bytes, filename: str, actor: str = "system") -> InvoiceRecord:
        if not content:
            raise UploadRejected("The file is empty.")
        if len(content) > self.settings.max_upload_bytes:
            limit_mb = self.settings.max_upload_bytes // (1024 * 1024)
            raise UploadRejected(f"The file is larger than {limit_mb} MB.")
        try:
            detected = detect(content)
        except UnsupportedDocument as exc:
            raise UploadRejected(str(exc)) from exc

        sha256 = hashlib.sha256(content).hexdigest()
        path = self._store_file(sha256, content)

        # Slow steps (LLM call, KoSIT in Docker) run before the database transaction.
        extraction, blocking = self._extract(detected, content)
        structured = check_structured(detected.kind, path, self.validator)

        invoice_id = uuid4().hex
        with self.database.connect() as conn:
            # Inserting first takes SQLite's write lock, so two uploads of the same invoice
            # cannot both read "no duplicate" before either has stored its keys.
            db.insert_invoice(
                conn,
                invoice_id=invoice_id,
                filename=filename,
                file_sha256=sha256,
                input_kind=detected.kind,
                actor=actor,
            )
            db.save_extraction(conn, invoice_id, extraction)
            rules = []
            if extraction is not None:
                db.set_status(
                    conn,
                    invoice_id,
                    InvoiceStatus.EXTRACTED,
                    actor="system",
                    note=extraction.source,
                )
                existing = db.existing_duplicate_keys(conn, exclude_id=invoice_id)
                rules = run_rules(extraction.data, today=self.today(), existing=existing)
                db.save_duplicate_keys(conn, invoice_id, duplicate_keys(extraction.data))
                ibans = None
                if self.settings.review_new_suppliers:
                    ibans = db.accepted_seller_ibans(
                        conn, seller_keys(extraction.data), exclude_id=invoice_id
                    )
                blocking += guardrail_reasons(extraction.data, self.settings, ibans)
            rules.append(structured)
            db.save_rules(conn, invoice_id, rules)

            decision = decide(extraction, rules, self.settings.confidence_threshold, blocking)
            new_status = (
                InvoiceStatus.AUTO_APPROVED if decision.auto_approve else InvoiceStatus.NEEDS_REVIEW
            )
            note = (
                "all fields confident, all rules passed"
                if decision.auto_approve
                else f"{len(decision.reasons)} problem(s)"
            )
            db.set_status(
                conn,
                invoice_id,
                new_status,
                actor="system",
                note=note,
                reasons=decision.reasons,
                blocking=blocking,
            )
            record = db.get_invoice(conn, invoice_id)
        assert record is not None
        return record

    def file_path(self, sha256: str) -> Path:
        return self.settings.files_dir / sha256

    def _store_file(self, sha256: str, content: bytes) -> Path:
        """Store under the content hash, never under the uploaded name (SPEC section 11)."""
        path = self.file_path(sha256)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.replace(tmp, path)
        return path

    def _extract(self, detected: Detected, content: bytes) -> tuple[Extraction | None, list[str]]:
        blocking: list[str] = []
        extraction = None
        try:
            if detected.kind is InputKind.PLAIN_PDF:
                extraction = extract_llm(content, self.llm)
            else:
                extraction = extract_xml(detected)
        except NoTextLayer as exc:
            blocking.append(str(exc))
        except LlmError as exc:
            blocking.append(f"LLM extraction failed: {exc}")
        if detected.is_credit_note:
            blocking.append("Credit notes are not supported yet.")
        return extraction, blocking
