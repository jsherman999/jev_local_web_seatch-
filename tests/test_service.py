import asyncio
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jev_service.app import create_app
from jev_service.jobs import JobManager
from jev_service.models import TERMINAL, JobRequest, Verification
from jev_service.worker import verify_page

COMMAND = [sys.executable, str(Path(__file__).with_name("fake_worker.py"))]
BODY = {"url": "https://example.com", "goal": "read"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1,192.168.4.27")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test-key")
    monkeypatch.delenv("JEV_API_TOKEN", raising=False)
    with TestClient(create_app(tmp_path, COMMAND)) as client:
        yield client


def poll(client, job_id, terminal=True):
    for _ in range(150):
        job = client.get("/v1/jobs/" + job_id).json()
        if (job["status"] in TERMINAL) if terminal else (job["status"] == "running"):
            return job
        time.sleep(0.02)
    pytest.fail("Job did not reach expected state")


def test_job_result_schema_and_delete(client):
    response = client.post("/v1/jobs", json=BODY)
    assert response.status_code == 202
    job_id = response.json()["id"]
    assert response.headers["location"] == "/v1/jobs/" + job_id
    job = poll(client, job_id)
    assert job["status"] == "completed"
    assert job["result"]["page"]["text"] == "Example Domain"
    assert client.get("/v1/jobs").json()[0]["id"] == job_id
    assert client.delete("/v1/jobs/" + job_id).status_code == 204
    assert client.get("/v1/jobs/" + job_id).status_code == 404
    schema = client.get("/openapi.json").json()
    assert schema["components"]["schemas"]["Job"]["properties"]["result"]
    assert client.get("/docs").status_code == 200
    assert "integration contract" in client.get("/integration.md").text


def test_cancel_queued_and_running(client):
    active = client.post("/v1/jobs", json={**BODY, "goal": "wait"}).json()["id"]
    poll(client, active, terminal=False)
    queued = client.post("/v1/jobs", json=BODY).json()["id"]
    assert client.get("/v1/jobs/" + queued).json()["status"] == "queued"
    assert client.delete("/v1/jobs/" + active).status_code == 409
    assert client.post("/v1/jobs/" + queued + "/cancel").json()["status"] == "cancelled"
    assert client.delete("/v1/jobs/" + queued).status_code == 204
    client.post("/v1/jobs/" + active + "/cancel")
    assert poll(client, active)["status"] == "cancelled"
    next_job = client.post("/v1/jobs", json=BODY).json()["id"]
    assert poll(client, next_job)["status"] == "completed"


def test_worker_crash_does_not_stop_queue(client):
    job_id = client.post("/v1/jobs", json={**BODY, "goal": "crash"}).json()["id"]
    assert poll(client, job_id)["status"] == "failed"
    job_id = client.post("/v1/jobs", json=BODY).json()["id"]
    assert poll(client, job_id)["status"] == "completed"


def test_auth_validation_and_origins(client, monkeypatch):
    monkeypatch.setenv("JEV_API_TOKEN", "secret")
    assert client.post("/v1/jobs", json=BODY).status_code == 401
    assert client.get("/v1/jobs", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.get("/v1/jobs", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert client.get("/health").status_code == 200
    monkeypatch.delenv("JEV_API_TOKEN")
    for bad in [{"url": "file:///etc/passwd"}, {"goal": " "}, {"timeout_seconds": 0}, {"unknown": 1}]:
        assert client.post("/v1/jobs", json={**BODY, **bad}).status_code == 422
    assert client.post("/v1/jobs", json=BODY, headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.get("/health", headers={"Host": "evil.test"}).status_code == 400
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert client.post("/v1/jobs", json=BODY).status_code == 503


def test_verification_distinguishes_done_from_evidence():
    page = {"url": "https://example.com/results", "text": "Example Domain"}
    assert verify_page(page, Verification())[0] == "not_requested"
    assert (
        verify_page(page, Verification(text_contains=["example domain"], url_contains="/results"))[0]
        == "passed"
    )
    assert verify_page(page, Verification(text_contains=["missing"]))[0] == "failed"


def test_restart_persistence_and_no_replay(tmp_path):
    async def scenario():
        manager = JobManager(tmp_path, COMMAND)
        job = manager.submit(JobRequest(**BODY))
        job["status"] = "running"
        manager.save(job)
        await manager.close()
        other = JobManager(tmp_path, COMMAND)
        other.start()
        assert other.get(job["id"])["status"] == "interrupted"
        assert other.queue.empty()
        await other.close()

    asyncio.run(scenario())


def test_timeout_and_queue_recovery(tmp_path):
    async def scenario():
        manager = JobManager(tmp_path, COMMAND)
        manager.start()
        request = JobRequest(**{**BODY, "goal": "timeout"})
        # Accelerate an internal timing test; public API enforces a minimum of 5 seconds.
        request.timeout_seconds = 0.1
        first = manager.submit(request)
        second = manager.submit(JobRequest(**BODY))
        await asyncio.wait_for(manager.queue.join(), timeout=5)
        assert manager.get(first["id"])["status"] == "timed_out"
        assert manager.get(second["id"])["status"] == "completed"
        await manager.close()

    asyncio.run(scenario())


def test_second_manager_cannot_recover_active_jobs(tmp_path):
    async def scenario():
        first = JobManager(tmp_path, COMMAND)
        with pytest.raises(RuntimeError, match="Another Jev API"):
            JobManager(tmp_path, COMMAND)
        await first.close()
        second = JobManager(tmp_path, COMMAND)
        await second.close()

    asyncio.run(scenario())


def test_listing_stays_in_submission_order(tmp_path):
    async def scenario():
        manager = JobManager(tmp_path, COMMAND)
        first = manager.submit(JobRequest(**BODY))
        second = manager.submit(JobRequest(**BODY))
        manager.save(first)
        assert [j["id"] for j in manager.list()] == [second["id"], first["id"]]
        await manager.close()

    asyncio.run(scenario())


def test_lan_and_localhost_docs_and_swagger_origin(client):
    for host in ["192.168.4.27:8776", "localhost:8776"]:
        headers = {"Host": host, "Origin": "http://" + host}
        assert client.get("/docs", headers=headers).status_code == 200
        assert client.get("/openapi.json", headers=headers).status_code == 200
        assert client.post("/v1/jobs", json=BODY, headers=headers).status_code == 202


def test_comparison_lifecycle_and_progress_merge(client):
    assert client.get("/demo").status_code == 200
    assert client.get("/demo/app.js").status_code == 200
    response = client.post("/v1/comparisons", json=BODY)
    assert response.status_code == 202
    comparison = response.json()
    job_id = comparison["id"]
    for _ in range(150):
        comparison = client.get("/v1/comparisons/" + job_id).json()
        if all(comparison[side]["status"] in TERMINAL for side in ("jev", "llm")):
            break
        time.sleep(.02)
    for side in ("jev", "llm"):
        job = comparison[side]
        assert job["status"] == "completed"
        assert job["request"]["capture_screenshots"] is True
        assert job["request"]["isolate_browser"] is True
        assert job["progress"]["execution_ms"] >= 0
        assert job["progress"]["started_at"]
        assert job["progress"]["steps"] == 1
        assert [e["phase"] for e in job["progress"]["trace"]] == ["start", "end"]
    assert comparison["jev"]["progress"]["comparison_baseline_id"] == comparison["llm"]["id"]
    assert client.delete("/v1/comparisons/" + job_id).status_code == 204
    assert client.get("/v1/comparisons/" + job_id).status_code == 404


def test_comparison_busy_cancel_and_auth(client, monkeypatch):
    monkeypatch.setenv("JEV_API_TOKEN", "secret")
    assert client.post("/v1/comparisons", json=BODY).status_code == 401
    headers = {"Authorization": "Bearer secret"}
    job_id = client.post("/v1/comparisons", json={**BODY, "goal": "wait"}, headers=headers).json()["id"]
    assert client.post("/v1/comparisons", json=BODY, headers=headers).status_code == 409
    assert client.get("/v1/comparisons/" + job_id).status_code == 401
    assert client.delete("/v1/comparisons/" + job_id, headers=headers).status_code == 409
    assert client.post("/v1/comparisons/" + job_id + "/cancel", headers=headers).status_code == 200
    for _ in range(150):
        comparison = client.get("/v1/comparisons/" + job_id, headers=headers).json()
        if all(comparison[side]["status"] == "cancelled" for side in ("jev", "llm")):
            break
        time.sleep(.02)
    assert comparison["jev"]["status"] == comparison["llm"]["status"] == "cancelled"


def test_function_source_is_allowlisted_and_authenticated(client, monkeypatch):
    assert client.get("/demo/flow.js").status_code == 200
    assert client.get("/demo/flow.css").status_code == 200
    assert client.get("/v1/demo/source/jev_choose").json()["code"].startswith("def choose(")
    assert client.get("/v1/demo/source/.env").status_code == 404
    monkeypatch.setenv("JEV_API_TOKEN", "secret")
    assert client.get("/v1/demo/source/jev_choose").status_code == 401
