import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_native_providers import fixture_transport

from agentbuddy.catalog.service import Catalog, DomainError
from agentbuddy.config import Settings
from agentbuddy.main import create_app
from agentbuddy.persistence.database import Database
from agentbuddy.persistence.migrate import upgrade
from agentbuddy.persistence.schema import projects, runs
from agentbuddy.providers.native import NativeModel
from agentbuddy.runtime.executor import Executor
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.worker.service import Worker


@pytest.fixture
def setup_chat(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.prepare_directories()
    upgrade(settings.database_path)
    db = Database(settings.database_path)
    yield settings, db
    db.close()


async def configure(db, kind="deepseek"):
    catalog = Catalog(db)
    provider = catalog.save_provider(
        dict(
            name="test",
            provider_type=kind,
            base_url="http://local.test",
            api_key="local-test-key",
            enabled=True,
            config_json={},
        )
    )
    model = catalog.save_model(
        dict(
            provider_id=provider["id"],
            model_id="test-model",
            enabled=True,
            context_window=32768,
            max_output_tokens=1024,
        )
    )

    def factory(snapshot, secret):
        return NativeModel(
            snapshot, secret, fixture_transport(snapshot["provider"]["provider_type"], [])
        )

    executor = Executor(db, factory)
    assert (await executor.model_probe(catalog.test_payload(model["id"])))["passed"]
    catalog.set_default(model["id"], 0)
    return provider, model, factory


@pytest.mark.parametrize("kind", ["deepseek", "qwen_dashscope", "claude"])
async def test_real_agentscope_chat_and_followup_with_sse_replay(setup_chat, kind):
    settings, db = setup_chat
    _, model, factory = await configure(db, kind)
    with TestClient(create_app(settings)) as client:
        payload = {"message": "你好", "model_id": model["id"]}
        first = client.post("/api/v1/runs", json=payload, headers={"Idempotency-Key": "first"})
        assert first.status_code == 202, first.text
        identifiers = first.json()["data"]
        assert (
            client.post("/api/v1/runs", json=payload, headers={"Idempotency-Key": "first"}).json()[
                "data"
            ]
            == identifiers
        )
        assert (
            client.post(
                "/api/v1/runs", json={"message": "changed"}, headers={"Idempotency-Key": "first"}
            ).status_code
            == 409
        )
        project_id, run_id = identifiers["project_id"], identifiers["run_id"]
        assert (
            client.post(
                "/api/v1/runs",
                json={**payload, "project_id": project_id},
                headers={"Idempotency-Key": "other"},
            ).status_code
            == 409
        )
        worker = Worker(settings)
        worker.executor.factory = factory
        await worker.run(once=True)
        result = client.get(f"/api/v1/runs/{run_id}").json()["data"]
        assert result["status"] == "succeeded", result
        assert "本地协议测试回答" in result["partial_text"]
        stream = client.get(f"/api/v1/runs/{run_id}/events").text
        assert "message.completed" in stream and "run.completed" in stream
        first_id = int(next(line[4:] for line in stream.splitlines() if line.startswith("id: ")))
        replay = client.get(
            f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": str(first_id)}
        ).text
        assert f"id: {first_id}\n" not in replay
        assert "local-test-key" not in stream + json.dumps(result)
        client.post(
            "/api/v1/runs",
            json={"project_id": project_id, "message": "继续"},
            headers={"Idempotency-Key": "second"},
        ).json()["data"]
        worker = Worker(settings)
        worker.executor.factory = factory
        await worker.run(once=True)
        history = client.get(f"/api/v1/projects/{project_id}/messages").json()["data"]
        assert len(history) == 4
        assert "已保留上轮上下文" in history[-1]["content"]
        with db.engine.connect() as conn:
            assert conn.scalar(select(func.count()).select_from(projects)) == 1
            assert conn.scalar(select(func.count()).select_from(runs)) == 2
    with TestClient(create_app(settings)) as client:
        assert len(client.get(f"/api/v1/projects/{project_id}/messages").json()["data"]) == 4


async def test_config_revisions_keys_and_snapshot(setup_chat):
    settings, db = setup_chat
    provider, model, factory = await configure(db)
    repo = RunRepository(db)
    created = repo.submit({"message": "snapshot", "model_id": model["id"]}, "snapshot")
    catalog = Catalog(db)
    updated = catalog.save_provider(
        dict(
            name="changed",
            provider_type="deepseek",
            base_url="http://changed.test",
            api_key="new-secret",
            enabled=True,
            config_json={},
            revision=1,
        ),
        provider["id"],
    )
    assert updated["revision"] == 2
    assert catalog.model_list()[0]["available"] is False
    with pytest.raises(DomainError):
        repo.submit({"message": "new", "model_id": model["id"]}, "new")
    snapshot = repo.get(created["run_id"])["snapshot_json"]
    assert snapshot["provider"]["base_url"] == "http://local.test"
    assert catalog.secret(snapshot["provider"]["credential_id"]) == "local-test-key"
    with TestClient(create_app(settings)) as client:
        assert "new-secret" not in client.get("/api/v1/providers").text
        bad = client.post("/api/v1/providers", json={"api_key": "do-not-echo"})
        assert bad.status_code == 422 and "do-not-echo" not in bad.text
    worker = Worker(settings)
    worker.executor.factory = factory
    await worker.run(once=True)
    assert repo.get(created["run_id"])["status"] == "succeeded"


async def test_no_model_no_project_and_failed_probe(setup_chat):
    import httpx

    settings, db = setup_chat
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/runs",
            json={"message": "must preserve draft"},
            headers={"Idempotency-Key": "none"},
        )
        assert response.status_code == 422
        assert client.get("/api/v1/projects").json()["data"] == []
    provider, model, _ = await configure(db)
    catalog = Catalog(db)
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="local-test-key"))
    executor = Executor(db, lambda snapshot, secret: NativeModel(snapshot, secret, transport))
    result = await executor.model_probe(catalog.test_payload(model["id"]))
    assert not result["passed"] and result["error"]["code"] == "authentication"
    assert "local-test-key" not in json.dumps(result)
    assert not catalog.model_list()[0]["available"]


async def test_run_interruption_and_context_limit(setup_chat):
    settings, db = setup_chat
    _, model, factory = await configure(db)
    from agentbuddy.persistence.repository import QueueRepository

    repository = RunRepository(db)
    worker_id = QueueRepository(db).start_worker()
    response = repository.submit({"message": "interrupted", "model_id": model["id"]}, "a")
    claimed = repository.claim(worker_id)
    repository.append(claimed, "partial")
    repository.interrupt(worker_id)
    assert not repository.finish(claimed, text="late")
    assert repository.get(response["run_id"])["status"] == "interrupted"
    large = repository.submit(
        {"message": "中" * 15000, "project_id": response["project_id"], "model_id": model["id"]},
        "b",
    )
    worker = Worker(settings)
    worker.executor.factory = factory
    await worker.run(once=True)
    assert repository.get(large["run_id"])["failure_json"]["code"] == "context_limit"
    assert len(repository.history(response["project_id"])) == 2


def test_upgrade_preserves_t02_operations(tmp_path):
    from agentbuddy.persistence.repository import QueueRepository

    path = tmp_path / "upgrade.sqlite3"
    upgrade(path, "0001_queue")
    db = Database(path)
    original = QueueRepository(db).enqueue("storage_check", {})
    db.close()
    upgrade(path)
    db = Database(path)
    assert QueueRepository(db).get(original["id"])["status"] == "queued"
    assert len(QueueRepository(db).events(original["id"])) == 1
    db.require_schema()
    db.close()


async def test_concurrent_idempotency_creates_one_project(setup_chat):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _, db = setup_chat
    _, model, _ = await configure(db)
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=5)
        return RunRepository(db).submit(
            {"message": "same request", "model_id": model["id"]}, "concurrent"
        )

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert results[0] == results[1]
    with db.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(projects)) == 1
        assert conn.scalar(select(func.count()).select_from(runs)) == 1


async def test_text_only_response_cannot_complete_file_contract(setup_chat):
    settings, db = setup_chat
    _, _, factory = await configure(db)
    repo = RunRepository(db)
    result = repo.submit({"message": "MISSING_TEST_FILE"}, "missing-output")
    worker = Worker(settings)
    worker.executor.factory = factory
    await worker.run(once=True)
    run = repo.get(result["run_id"])
    assert run["status"] == "failed"
    assert run["failure_json"]["code"] == "delivery_incomplete"
