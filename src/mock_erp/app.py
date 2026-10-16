"""Mock ERP: stores approved invoices, idempotent on the Idempotency-Key header.

POST /api/invoices      store an invoice (201), or return the stored one for a known key (200)
GET  /api/invoices      list what arrived
GET  /api/invoices/{id} one invoice

Flaky mode (`fail_every=n`) answers every n-th POST with 503, to test retries.
Demo mode (`demo=True`) adds POST /api/reset for the hourly demo reset. The mock ERP only
listens on 127.0.0.1, so the endpoint is not reachable from outside the container.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, Header, HTTPException, Response

SCHEMA = """
CREATE TABLE IF NOT EXISTS erp_invoices (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    received_at     TEXT NOT NULL,
    payload_sha256  TEXT NOT NULL,
    payload_json    TEXT NOT NULL
);
"""


def _erp_id(seq: int) -> str:
    return f"ERP-{seq:06d}"


def _payload_sha(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def create_app(db_path: Path, fail_every: int = 0, demo: bool = False) -> FastAPI:
    app = FastAPI(title="mock ERP", version="0.1.0")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    state = {"posts": 0, "fail_every": fail_every}
    app.state.erp = state

    @contextmanager
    def connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    with connect() as conn:
        conn.executescript(SCHEMA)

    def to_json(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "erp_id": _erp_id(row["seq"]),
            "idempotency_key": row["idempotency_key"],
            "received_at": row["received_at"],
            "invoice": json.loads(row["payload_json"]),
        }

    @app.post("/api/invoices")
    def receive(
        payload: Annotated[dict[str, Any], Body()],
        response: Response,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if not idempotency_key:
            raise HTTPException(400, "Idempotency-Key header is required.")
        # One request at a time: keeps the flaky counter and the key check consistent.
        with lock:
            state["posts"] += 1
            if state["fail_every"] and state["posts"] % state["fail_every"] == 0:
                raise HTTPException(503, "ERP temporarily unavailable (flaky mode).")
            sha = _payload_sha(payload)
            with connect() as conn:
                row = conn.execute(
                    "SELECT * FROM erp_invoices WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if row is not None:
                    if row["payload_sha256"] != sha:
                        raise HTTPException(
                            409, "Idempotency-Key was already used with a different invoice."
                        )
                    response.status_code = 200
                    return {**to_json(row), "created": False}
                conn.execute(
                    "INSERT INTO erp_invoices (idempotency_key, received_at, payload_sha256,"
                    " payload_json) VALUES (?, ?, ?, ?)",
                    (
                        idempotency_key,
                        datetime.now(UTC).isoformat(timespec="seconds"),
                        sha,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM erp_invoices WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
        response.status_code = 201
        return {**to_json(row), "created": True}

    if demo:

        @app.post("/api/reset")
        def reset() -> dict[str, int]:
            with lock, connect() as conn:
                deleted = conn.execute("DELETE FROM erp_invoices").rowcount
                conn.execute("DELETE FROM sqlite_sequence WHERE name = 'erp_invoices'")
            return {"deleted": deleted}

    @app.get("/api/invoices")
    def list_invoices() -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute("SELECT * FROM erp_invoices ORDER BY seq").fetchall()
        return [to_json(r) for r in rows]

    @app.get("/api/invoices/{erp_id}")
    def get_invoice(erp_id: str) -> dict[str, Any]:
        try:
            seq = int(erp_id.removeprefix("ERP-"))
        except ValueError:
            raise HTTPException(404, "Unknown ERP id.") from None
        with connect() as conn:
            row = conn.execute("SELECT * FROM erp_invoices WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown ERP id.")
        return to_json(row)

    return app
