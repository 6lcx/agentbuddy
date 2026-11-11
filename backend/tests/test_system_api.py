from fastapi.testclient import TestClient

from agentbuddy.config import Settings
from agentbuddy.main import create_app
from agentbuddy.persistence.migrate import upgrade
from agentbuddy.worker.service import Worker


async def test_api_to_worker_to_persisted_result(tmp_path):
    settings = Settings(data_dir=tmp_path, api_prefix="/custom/api/v1")
    settings.prepare_directories()
    upgrade(settings.database_path)
    prefix = settings.api_prefix
    with TestClient(create_app(settings)) as client:
        assert client.get(f"{prefix}/system/status").json()["worker"]["status"] == "not_started"
        response = client.post(f"{prefix}/system/diagnostics")
        assert response.status_code == 202
        operation_id = response.json()["id"]
        assert client.get(f"{prefix}/operations/{operation_id}").json()["status"] == "queued"
        await Worker(settings).run(once=True)
        result = client.get(f"{prefix}/operations/{operation_id}").json()
        assert result["status"] == "succeeded"
        assert "claim_token" not in result
        assert "payload_json" not in result
        events = client.get(f"{prefix}/operations/{operation_id}/events").json()["items"]
        assert len(events) == 3
        after = events[0]["id"]
        tail = client.get(f"{prefix}/operations/{operation_id}/events?after={after}&limit=1").json()
        assert tail["items"][0]["type"] == "operation.running"
        assert client.get(f"{prefix}/operations/missing").status_code == 404
        assert client.get(f"{prefix}/operations/{operation_id}/events?limit=501").status_code == 422
    with TestClient(create_app(settings)) as client:
        assert client.get(f"{prefix}/operations/{operation_id}").json()["status"] == "succeeded"
        assert client.get(f"{prefix}/system/status").json()["worker"]["status"] == "stopped"


def test_uninitialized_db_is_explicit_and_health_still_alive(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/system/status").status_code == 503
        assert client.post("/api/v1/system/diagnostics").status_code == 503
    assert not settings.database_path.exists()
