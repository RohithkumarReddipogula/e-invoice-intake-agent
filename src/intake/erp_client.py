"""Send approved invoices to the ERP (SPEC section 8.3).

Timeout per request, 3 retries with exponential backoff on network errors, timeouts, 429
and 5xx. Every request carries `Idempotency-Key: <invoice id>`, so a retry after a lost
response never creates a second invoice in the ERP.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

RETRIES = 3
TIMEOUT_SECONDS = 5.0
BACKOFF_SECONDS = 0.5


class ErpError(Exception):
    def __init__(self, message: str, attempts: int):
        super().__init__(message)
        self.attempts = attempts


@dataclass
class ExportResult:
    erp_id: str
    attempts: int
    created: bool  # False: the ERP already had it (an earlier response was lost)


class ErpClient:
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        retries: int = RETRIES,
        timeout: float = TIMEOUT_SECONDS,
        backoff: float = BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        # An injected client (tests) brings its own transport and timeout.
        self.client = client or httpx.Client(timeout=timeout)
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep

    def send(self, invoice_id: str, payload: dict[str, Any]) -> ExportResult:
        last_problem = ""
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.post(
                    f"{self.base_url}/api/invoices",
                    json=payload,
                    headers={"Idempotency-Key": invoice_id},
                )
            except httpx.TransportError as exc:  # connection refused, timeouts, ...
                last_problem = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code in (200, 201):
                    return ExportResult(
                        erp_id=response.json()["erp_id"],
                        attempts=attempt,
                        created=response.status_code == 201,
                    )
                last_problem = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code != 429 and response.status_code < 500:
                    # The ERP understood and refused; sending again will not help.
                    raise ErpError(f"ERP rejected the invoice: {last_problem}", attempt)
            if attempt < attempts:
                self.sleep(self.backoff * 2 ** (attempt - 1))
        raise ErpError(f"ERP not reachable after {attempts} attempts: {last_problem}", attempts)
