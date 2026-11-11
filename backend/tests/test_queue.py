import multiprocessing
from concurrent.futures import ProcessPoolExecutor

from sqlalchemy import inspect, select, text

from agentbuddy.persistence.database import Database
from agentbuddy.persistence.migrate import upgrade
from agentbuddy.persistence.repository import QueueRepository


def claim_in_process(path, barrier):
    db = Database(path)
    repo = QueueRepository(db)
    worker = repo.start_worker()
    barrier.wait(timeout=20)
    operation = repo.claim(worker)
    db.close()
    return operation["id"] if operation else None


def test_migration_and_atomic_process_claim(tmp_path):
    path = tmp_path / "queue.sqlite3"
    upgrade(path)
    upgrade(path)
    db = Database(path)
    assert {"operations", "events", "worker_instances"} <= set(inspect(db.engine).get_table_names())
    with db.engine.connect() as conn:
        assert conn.scalar(text("PRAGMA journal_mode")) == "wal"
        assert conn.scalar(text("PRAGMA foreign_keys")) == 1
    repo = QueueRepository(db)
    operation = repo.enqueue("storage_check", {})
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager:
        barrier = manager.Barrier(2)
        with ProcessPoolExecutor(2, mp_context=context) as pool:
            futures = [pool.submit(claim_in_process, path, barrier) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
    assert results.count(operation["id"]) == 1
    assert results.count(None) == 1
    assert [event["type"] for event in repo.events(operation["id"])] == [
        "operation.queued",
        "operation.running",
    ]
    db.close()


def test_recovery_does_not_replay_and_rejects_late_result(tmp_path):
    path = tmp_path / "queue.sqlite3"
    upgrade(path)
    db = Database(path)
    repo = QueueRepository(db)
    worker = repo.start_worker(now=100)
    first = repo.enqueue("storage_check", {})
    waiting = repo.enqueue("storage_check", {})
    claimed = repo.claim(worker)
    repo.recover_previous_workers(now=200)
    assert repo.get(first["id"])["status"] == "failed"
    assert repo.get(waiting["id"])["status"] == "queued"
    assert not repo.finish(claimed, result={"late": True})
    assert repo.get(first["id"])["error_json"]["code"] == "worker_interrupted"
    assert repo.claim(worker) is None
    new_worker = repo.start_worker(now=201)
    assert repo.claim(new_worker)["id"] == waiting["id"]
    db.close()


def test_queue_and_event_commit_is_atomic(tmp_path, monkeypatch):
    import pytest
    from sqlalchemy import func

    from agentbuddy.persistence.schema import events, operations

    path = tmp_path / "queue.sqlite3"
    upgrade(path)
    db = Database(path)
    repo = QueueRepository(db)

    def fail(*args):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(repo, "_event", fail)
    with pytest.raises(RuntimeError, match="event write failed"):
        repo.enqueue("storage_check", {})
    with db.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(operations)) == 0
        assert conn.scalar(select(func.count()).select_from(events)) == 0
    db.close()


def test_migration_matches_metadata_and_rejects_wrong_claim_token(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from agentbuddy.persistence.schema import metadata

    path = tmp_path / "queue.sqlite3"
    upgrade(path)
    db = Database(path)
    with db.engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), metadata) == []
    repo = QueueRepository(db)
    worker = repo.start_worker()
    item = repo.enqueue("storage_check", {})
    claimed = repo.claim(worker)
    assert not repo.finish({**claimed, "claim_token": "wrong"}, result={})
    assert repo.get(item["id"])["status"] == "running"
    assert repo.finish(claimed, result={})
    assert not repo.finish(claimed, result={"duplicate": True})
    assert len(repo.events(item["id"])) == 3
    db.close()


def test_migration_releases_database_handles_without_gc(tmp_path):
    import gc

    path = tmp_path / "migration.sqlite3"
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        upgrade(path)
        # Windows cannot rename an open SQLite file; migration must close every handle.
        path.rename(tmp_path / "closed.sqlite3")
    finally:
        if was_enabled:
            gc.enable()
