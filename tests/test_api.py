import json
import time

import pytest
from fastapi.testclient import TestClient

from researchos.api.app import create_app
from researchos.infra.config import Settings


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        yield client


def complete(client):
    response = client.post("/api/research", json={"topic": "Agent Memory"})
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    run_id = response.json()["id"]
    for _ in range(100):
        run = client.get(f"/api/research/{run_id}").json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.01)
    raise AssertionError("job failed to finish")


def test_api_complete_exports_history_memory_sse(client):
    assert client.get("/health").json()["status"] == "ok"
    run = complete(client)
    assert run["status"] == "completed" and run["checks"]["passed"]
    base = f"/api/research/{run['id']}"
    assert len(client.get(base + "/sources").json()) == 6
    assert client.get(base + "/memory", params={"q": "SQLite"}).json()
    assert "DEMO / OFFLINE" in client.get(base + "/report?format=md").text
    assert "<table>" in client.get(base + "/report?format=html").text
    assert client.get(base + "/trace?format=agentops").json()["trace"]
    events = client.get(base + "/events")
    assert events.headers["content-type"].startswith("text/event-stream")
    rows = [json.loads(line[6:]) for line in events.text.splitlines() if line.startswith("data: ")]
    assert any(row["kind"] == "plan" for row in rows)
    assert rows[-1]["kind"] == "job_finished"
    last = rows[-1]["seq"]
    assert "data:" not in client.get(base + "/events", headers={"Last-Event-ID": str(last)}).text
    assert client.get("/api/research").json()[0]["id"] == run["id"]


def test_api_errors_constraints_and_live_gate(client):
    assert client.post("/api/research", json={"topic": "  "}).status_code == 422
    assert client.post("/api/research", json={"topic": "test", "mode": "live"}).status_code == 403
    assert client.get("/api/research/missing").status_code == 404
    response = client.post("/api/research", content="x" * 33000)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "body_too_large"


def test_auth_protects_history_jobs_and_events(tmp_path):
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path, api_key="test-key-no-real-secret"))
    ) as client:
        assert client.get("/api/config").json()["auth_required"]
        assert client.get("/health").status_code == 200
        assert client.get("/api/research").status_code == 401
        headers = {"Authorization": "Bearer test-key-no-real-secret"}
        assert client.get("/api/research", headers=headers).status_code == 200


def test_agentops_compatibility_rejects_unsupported_flags(client):
    response = client.post("/api/agentops/research", json={"task": "Agent Memory"})
    assert response.status_code == 200
    assert set(response.json()) == {"answer", "trace"}
    assert (
        client.post(
            "/api/agentops/research", json={"task": "Agent Memory", "config": {"planner": False}}
        ).status_code
        == 422
    )


def test_cancel_and_not_ready_report(client):
    # A queued job snapshot is created before the asynchronous worker is scheduled.
    result = client.post("/api/research", json={"topic": "Agent Memory", "depth": "deep"})
    run_id = result.json()["id"]
    assert client.post(f"/api/research/{run_id}/cancel").json()["status"] in {
        "cancelled",
        "completed",
        "partial",
    }
    assert (
        client.get(
            f"/api/research/{run_id}/events", headers={"Last-Event-ID": "invalid"}
        ).status_code
        == 400
    )


def test_agentops_whitespace_is_422_and_untrusted_host_rejected(client):
    assert client.post("/api/agentops/research", json={"task": "   "}).status_code == 422
    assert (
        client.get("/api/research", headers={"Host": "untrusted-attacker.example"}).status_code
        == 400
    )
