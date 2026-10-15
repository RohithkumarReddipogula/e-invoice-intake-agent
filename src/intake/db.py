"""SQLite storage. Plain SQL, migrations tracked with PRAGMA user_version."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from .models import Extraction, InvoiceData
from .records import Event, InvoiceRecord, InvoiceSummary
from .rules import DuplicateKey, RuleResult, RuleStatus
from .status import InvoiceStatus


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _migrations() -> list[tuple[int, str]]:
    files = resources.files("intake") / "migrations"
    found = []
    for entry in files.iterdir():
        if entry.name.endswith(".sql"):
            found.append((int(entry.name.split("_", 1)[0]), entry.read_text(encoding="utf-8")))
    return sorted(found)


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """One connection per unit of work; commits on success, rolls back on error."""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connect() as conn:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for version, sql in _migrations():
                if version > current:
                    conn.executescript(sql)
                    conn.execute(f"PRAGMA user_version = {version:d}")


# --- writes ----------------------------------------------------------------------------


def insert_invoice(
    conn: sqlite3.Connection,
    *,
    invoice_id: str,
    filename: str,
    file_sha256: str,
    input_kind: str,
    actor: str = "system",
) -> None:
    ts = now()
    conn.execute(
        "INSERT INTO invoices (id, created_at, updated_at, filename, file_sha256, input_kind,"
        " status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (invoice_id, ts, ts, filename, file_sha256, input_kind, InvoiceStatus.RECEIVED),
    )
    add_event(conn, invoice_id, actor, None, InvoiceStatus.RECEIVED, f"uploaded {filename!r}")


def save_extraction(
    conn: sqlite3.Connection,
    invoice_id: str,
    extraction: Extraction | None,
    corrected_by: dict[str, str] | None = None,
) -> None:
    """Replace the stored data and field confidences. `corrected_by`: field -> actor."""
    corrected_by = corrected_by or {}
    conn.execute("DELETE FROM fields WHERE invoice_id = ?", (invoice_id,))
    if extraction is None:
        conn.execute(
            "UPDATE invoices SET source = NULL, data_json = NULL, updated_at = ? WHERE id = ?",
            (now(), invoice_id),
        )
        return
    conn.execute(
        "UPDATE invoices SET source = ?, data_json = ?, updated_at = ? WHERE id = ?",
        (extraction.source, extraction.data.model_dump_json(), now(), invoice_id),
    )
    conn.executemany(
        "INSERT INTO fields (invoice_id, name, confidence, note, corrected_by)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (invoice_id, name, conf, extraction.notes.get(name), corrected_by.get(name))
            for name, conf in extraction.confidence.items()
        ],
    )


def save_rules(conn: sqlite3.Connection, invoice_id: str, rules: list[RuleResult]) -> None:
    conn.execute("DELETE FROM rule_results WHERE invoice_id = ?", (invoice_id,))
    conn.executemany(
        "INSERT INTO rule_results (invoice_id, rule_id, name, status, reason)"
        " VALUES (?, ?, ?, ?, ?)",
        [(invoice_id, r.rule_id, r.name, r.status, r.reason) for r in rules],
    )


def save_duplicate_keys(conn: sqlite3.Connection, invoice_id: str, keys: set[DuplicateKey]) -> None:
    conn.execute("DELETE FROM duplicate_keys WHERE invoice_id = ?", (invoice_id,))
    conn.executemany(
        "INSERT INTO duplicate_keys (invoice_id, seller_key, invoice_number) VALUES (?, ?, ?)",
        [(invoice_id, seller, number) for seller, number in sorted(keys)],
    )


def set_status(
    conn: sqlite3.Connection,
    invoice_id: str,
    new_status: InvoiceStatus,
    *,
    actor: str,
    note: str | None = None,
    reasons: list[str] | None = None,
    blocking: list[str] | None = None,
) -> None:
    row = conn.execute("SELECT status FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    old_status = row["status"] if row else None
    updates = ["status = ?", "updated_at = ?"]
    params: list[object] = [new_status, now()]
    if reasons is not None:
        updates.append("reasons_json = ?")
        params.append(json.dumps(reasons, ensure_ascii=False))
    if blocking is not None:
        updates.append("blocking_json = ?")
        params.append(json.dumps(blocking, ensure_ascii=False))
    conn.execute(f"UPDATE invoices SET {', '.join(updates)} WHERE id = ?", [*params, invoice_id])
    add_event(conn, invoice_id, actor, old_status, new_status, note)


def add_event(
    conn: sqlite3.Connection,
    invoice_id: str,
    actor: str,
    old_status: str | None,
    new_status: str,
    note: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO events (invoice_id, at, actor, old_status, new_status, note)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (invoice_id, now(), actor, old_status, new_status, note),
    )


# --- reads -----------------------------------------------------------------------------


def existing_duplicate_keys(conn: sqlite3.Connection, exclude_id: str) -> set[DuplicateKey]:
    """Keys of all other invoices that are not rejected (R6 ignores rejected invoices)."""
    rows = conn.execute(
        "SELECT k.seller_key, k.invoice_number FROM duplicate_keys k"
        " JOIN invoices i ON i.id = k.invoice_id"
        " WHERE i.status != ? AND i.id != ?",
        (InvoiceStatus.REJECTED, exclude_id),
    )
    return {(r["seller_key"], r["invoice_number"]) for r in rows}


def get_invoice(conn: sqlite3.Connection, invoice_id: str) -> InvoiceRecord | None:
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if row is None:
        return None
    fields = conn.execute(
        "SELECT name, confidence, note, corrected_by FROM fields WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchall()
    rules = conn.execute(
        "SELECT rule_id, name, status, reason FROM rule_results WHERE invoice_id = ?"
        " ORDER BY rule_id",
        (invoice_id,),
    ).fetchall()
    events = conn.execute(
        "SELECT at, actor, old_status, new_status, note FROM events WHERE invoice_id = ?"
        " ORDER BY id",
        (invoice_id,),
    ).fetchall()
    return InvoiceRecord(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        filename=row["filename"],
        file_sha256=row["file_sha256"],
        input_kind=row["input_kind"],
        status=InvoiceStatus(row["status"]),
        source=row["source"],
        data=InvoiceData.model_validate_json(row["data_json"]) if row["data_json"] else None,
        confidence={f["name"]: f["confidence"] for f in fields},
        notes={f["name"]: f["note"] for f in fields if f["note"]},
        corrected_by={f["name"]: f["corrected_by"] for f in fields if f["corrected_by"]},
        rules=[
            RuleResult(
                rule_id=r["rule_id"],
                name=r["name"],
                status=RuleStatus(r["status"]),
                reason=r["reason"],
            )
            for r in rules
        ],
        blocking=json.loads(row["blocking_json"]),
        reasons=json.loads(row["reasons_json"]),
        events=[Event(**dict(e)) for e in events],
    )


def list_invoices(
    conn: sqlite3.Connection, status: InvoiceStatus | None = None, limit: int = 20
) -> list[InvoiceSummary]:
    sql = "SELECT id, created_at, status, data_json, reasons_json FROM invoices"
    params: list[object] = []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(limit)
    summaries = []
    for row in conn.execute(sql, params):
        data = InvoiceData.model_validate_json(row["data_json"]) if row["data_json"] else None
        reasons = json.loads(row["reasons_json"])
        summaries.append(
            InvoiceSummary(
                id=row["id"],
                created_at=row["created_at"],
                status=InvoiceStatus(row["status"]),
                invoice_number=data.invoice_number if data else None,
                seller_name=data.seller_name if data else None,
                gross_total=data.gross_total if data else None,
                currency=data.currency if data else None,
                main_reason=reasons[0] if reasons else None,
            )
        )
    return summaries
