from fastapi.testclient import TestClient

from mock_erp.app import create_app

PAYLOAD = {"invoice_id": "abc", "invoice": {"invoice_number": "RE-1", "gross_total": "119.00"}}


def post(client, payload=PAYLOAD, key="abc"):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/api/invoices", json=payload, headers=headers)


def test_create_then_idempotent_repeat(tmp_path):
    client = TestClient(create_app(tmp_path / "erp.db"))
    first = post(client)
    assert first.status_code == 201
    assert first.json()["erp_id"] == "ERP-000001"
    assert first.json()["created"] is True
    again = post(client)
    assert again.status_code == 200
    assert again.json()["erp_id"] == "ERP-000001"
    assert again.json()["created"] is False
    assert len(client.get("/api/invoices").json()) == 1


def test_same_key_different_invoice_is_a_conflict(tmp_path):
    client = TestClient(create_app(tmp_path / "erp.db"))
    post(client)
    changed = {**PAYLOAD, "invoice": {**PAYLOAD["invoice"], "gross_total": "999.00"}}
    assert post(client, changed).status_code == 409


def test_key_is_required(tmp_path):
    client = TestClient(create_app(tmp_path / "erp.db"))
    assert post(client, key=None).status_code == 400


def test_flaky_mode_fails_every_nth_post(tmp_path):
    client = TestClient(create_app(tmp_path / "erp.db", fail_every=3))
    codes = [post(client, key=f"k{i}").status_code for i in range(6)]
    assert codes == [201, 201, 503, 201, 201, 503]


def test_data_survives_a_restart(tmp_path):
    post(TestClient(create_app(tmp_path / "erp.db")))
    restarted = TestClient(create_app(tmp_path / "erp.db"))
    assert restarted.get("/api/invoices/ERP-000001").json()["invoice"] == PAYLOAD
    assert restarted.get("/api/invoices/ERP-000002").status_code == 404
    assert restarted.get("/api/invoices/nonsense").status_code == 404
