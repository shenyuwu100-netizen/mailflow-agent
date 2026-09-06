import pytest
from mailflow.app import create_app


@pytest.fixture
def client(tmp_path):
    return create_app(tmp_path / "web.db").test_client()


def post(client, path, data):
    return client.post(path, json=data, headers={"X-MailFlow-Demo": "1"})


def test_demo_end_to_end_and_export(client):
    assert client.get("/").status_code == 200
    safe = post(client, "/api/ingest", {"subject": "如何查看物流", "body": "如何查看物流", "automatic": True}).json
    assert safe["status"] == "simulated_sent"
    risky = post(client, "/api/ingest", {"subject": "退款", "body": "如何查看物流？我要退款", "automatic": True}).json
    assert risky["status"] == "review"
    approved = post(client, f'/api/jobs/{risky["id"]}/review', {"action": "approve", "reply": "已核实回复", "version": risky["version"]})
    assert approved.json["status"] == "simulated_sent"
    assert len(client.get("/api/jobs").json["jobs"]) == 2
    assert len(client.get("/api/export").json) == 2


def test_request_boundaries(client):
    assert client.post("/api/ingest", json={}).status_code == 403
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 403
    assert client.post("/api/ingest", json={}, headers={"X-MailFlow-Demo": "1", "Origin": "https://attacker.example"}).status_code == 403
    assert post(client, "/api/ingest", {"subject": [], "body": "x"}).status_code == 400


def test_duplicate_post_does_not_simulate_again(client):
    data = {"message_id": "repeat", "subject": "如何查看物流", "body": "如何查看物流", "automatic": True}
    a = post(client, "/api/ingest", data).json
    b = post(client, "/api/ingest", data).json
    assert a["id"] == b["id"] and len(b["events"]) == 3


def test_live_model_budget_is_enforced(tmp_path):
    from unittest.mock import patch
    client = create_app(tmp_path / "budget.db", live_model=True, max_model_calls=1).test_client()
    with patch("mailflow.model.propose", return_value={"error": "test_failure"}) as call:
        data = {"subject": "test", "body": "test", "automatic": True}
        assert post(client, "/api/ingest", data).json["status"] == "review"
        second = post(client, "/api/ingest", data).json
    assert call.call_count == 1
    assert second["decision"]["analysis"]["error"] == "model_call_budget_exhausted"
