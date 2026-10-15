"""The real mock ERP app, reached in-process, plus a wrapper that loses responses."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient

from intake.erp_client import ErpClient
from mock_erp.app import create_app


class Erp:
    def __init__(self, tmp_path, fail_every: int = 0):
        self.app = create_app(tmp_path / "erp.db", fail_every)
        self.http = TestClient(self.app)
        self.sleeps: list[float] = []

    def set_fail_every(self, n: int) -> None:
        self.app.state.erp["fail_every"] = n

    def client(self, http: Any = None) -> ErpClient:
        return ErpClient("http://testserver", client=http or self.http, sleep=self.sleeps.append)

    def invoices(self) -> list[dict[str, Any]]:
        return self.http.get("/api/invoices").json()


class LosesFirstResponse:
    """Delivers the first request to the ERP, then acts as if the response timed out."""

    def __init__(self, http: TestClient):
        self.http = http
        self.calls = 0

    def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
        self.calls += 1
        response = self.http.post(*args, **kwargs)
        if self.calls == 1:
            raise httpx.ReadTimeout("response lost on the way back")
        return response
