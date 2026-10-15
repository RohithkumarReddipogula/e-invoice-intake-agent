"""Retries, backoff and the idempotency key, with httpx.MockTransport."""

import httpx
import pytest

from intake.erp_client import ErpClient, ErpError

OK = httpx.Response(201, json={"erp_id": "ERP-000001"})


def client_with(responses, sleeps=None):
    """Answers requests from `responses` in order; an exception instance is raised."""
    seen = []

    def handler(request):
        seen.append(request)
        item = responses[len(seen) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    http = httpx.Client(transport=httpx.MockTransport(handler))
    erp = ErpClient(
        "http://erp.test/", client=http, sleep=(sleeps if sleeps is not None else []).append
    )
    return erp, seen


def test_default_client_has_a_timeout():
    assert ErpClient("http://erp.test").client.timeout == httpx.Timeout(5.0)


def test_success_first_try():
    erp, seen = client_with([OK])
    result = erp.send("inv-1", {"x": 1})
    assert (result.erp_id, result.attempts, result.created) == ("ERP-000001", 1, True)
    (request,) = seen
    assert str(request.url) == "http://erp.test/api/invoices"
    assert request.headers["Idempotency-Key"] == "inv-1"


def test_already_in_erp():
    erp, _ = client_with([httpx.Response(200, json={"erp_id": "ERP-000007"})])
    result = erp.send("inv-1", {})
    assert (result.erp_id, result.created) == ("ERP-000007", False)


@pytest.mark.parametrize(
    "problem",
    [
        httpx.Response(503, text="busy"),
        httpx.Response(429, text="slow down"),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("too slow"),
    ],
)
def test_transient_problems_are_retried_with_backoff(problem):
    sleeps = []
    erp, seen = client_with([problem, problem, OK], sleeps)
    result = erp.send("inv-1", {})
    assert result.attempts == 3
    assert sleeps == [0.5, 1.0]
    assert {r.headers["Idempotency-Key"] for r in seen} == {"inv-1"}


def test_gives_up_after_three_retries():
    sleeps = []
    erp, seen = client_with([httpx.Response(503, text="down")] * 4, sleeps)
    with pytest.raises(ErpError, match="not reachable after 4 attempts: HTTP 503: down") as info:
        erp.send("inv-1", {})
    assert info.value.attempts == 4
    assert len(seen) == 4
    assert sleeps == [0.5, 1.0, 2.0]  # no sleep after the last attempt


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_client_errors_are_not_retried(status):
    erp, seen = client_with([httpx.Response(status, text="no")])
    with pytest.raises(ErpError, match=f"rejected the invoice: HTTP {status}") as info:
        erp.send("inv-1", {})
    assert info.value.attempts == 1
    assert len(seen) == 1
