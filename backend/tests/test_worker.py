import asyncio
import os
import subprocess
import sys
import time

import pytest
from filelock import FileLock, Timeout
from sqlalchemy import text

from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.persistence.migrate import upgrade
from agentbuddy.persistence.repository import QueueRepository
from agentbuddy.worker.service import Worker


@pytest.fixture
def setup_queue(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        worker_poll_seconds=0.02,
        worker_heartbeat_seconds=0.05,
        worker_stale_seconds=1,
        worker_shutdown_seconds=0.05,
    )
    settings.prepare_directories()
    upgrade(settings.database_path)
    database = Database(settings.database_path)
    yield settings, QueueRepository(database)
    database.close()


def wait_until(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.03)
    raise AssertionError("Timed out waiting for child process state")


def test_independent_worker_and_exclusive_lock(setup_queue):
    settings, repo = setup_queue
    operation = repo.enqueue("storage_check", {})
    env = {**os.environ, "DATA_DIR": str(settings.data_dir)}
    child = subprocess.Popen(
        [sys.executable, "-m", "agentbuddy.worker"],
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        wait_until(lambda: repo.get(operation["id"])["status"] == "succeeded")
        result = repo.get(operation["id"])["result_json"]
        assert result == {"storage": "ok", "journal_mode": "wal", "foreign_keys": True}
        duplicate = subprocess.run(
            [sys.executable, "-m", "agentbuddy.worker", "--once"],
            env=env,
            capture_output=True,
            timeout=10,
        )
        assert duplicate.returncode == 2
        assert b"already running" in duplicate.stderr
        with pytest.raises(Timeout):
            upgrade(settings.database_path)
        assert repo.worker_status()["status"] == "running"
    finally:
        child.kill()
        child.wait(timeout=10)


def test_crash_restart_preserves_queued_and_fails_inflight(setup_queue):
    settings, repo = setup_queue
    inflight = repo.enqueue("blocking_test", {})
    script = (
        "import asyncio; from agentbuddy.config import Settings; "
        "from agentbuddy.worker.service import Worker; "
        "asyncio.run(Worker(Settings(), {'blocking_test': lambda p: asyncio.sleep(100)}).run())"
    )
    env = {**os.environ, "DATA_DIR": str(settings.data_dir)}
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        wait_until(lambda: repo.get(inflight["id"])["status"] == "running")
        queued = repo.enqueue("storage_check", {})
    finally:
        child.kill()
        child.wait(timeout=10)
    result = subprocess.run(
        [sys.executable, "-m", "agentbuddy.worker", "--once"],
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert repo.get(inflight["id"])["error_json"]["code"] == "worker_interrupted"
    assert repo.get(queued["id"])["status"] == "succeeded"
    assert repo.worker_status()["status"] == "stopped"
    assert [event["type"] for event in repo.events(inflight["id"])] == [
        "operation.queued",
        "operation.running",
        "operation.failed",
    ]


async def test_heartbeat_during_work_and_bounded_shutdown(setup_queue):
    settings, repo = setup_queue
    entered = asyncio.Event()

    async def slow(payload):
        entered.set()
        await asyncio.sleep(100)
        return {}

    operation = repo.enqueue("slow", {})
    stop = asyncio.Event()
    worker = Worker(settings, {"slow": slow})
    task = asyncio.create_task(worker.run(stop))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        first = repo.worker_status()["heartbeat_at"]
        await asyncio.sleep(0.15)
        assert repo.worker_status()["heartbeat_at"] > first
        # Handler wait must not hold a write transaction.
        other = repo.enqueue("storage_check", {})
        stop.set()
        await asyncio.wait_for(task, 5)
        assert repo.get(operation["id"])["status"] == "failed"
        assert repo.get(other["id"])["status"] == "queued"
        with FileLock(str(settings.database_path) + ".worker.lock", timeout=0):
            pass
    finally:
        stop.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_timeout_and_unknown_handler_are_terminal(setup_queue):
    settings, repo = setup_queue
    settings.operation_timeout_seconds = 0.03
    operation = repo.enqueue("slow", {})
    await Worker(settings, {"slow": lambda _: asyncio.sleep(10)}).run(once=True)
    assert repo.get(operation["id"])["error_json"]["code"] == "operation_timeout"
    unsupported = repo.enqueue("missing", {})
    await Worker(settings).run(once=True)
    assert repo.get(unsupported["id"])["error_json"]["code"] == "unsupported_operation"


def test_stale_heartbeat_and_foreign_keys(setup_queue):
    _, repo = setup_queue
    worker = repo.start_worker(now=100)
    assert repo.worker_status(now=131)["status"] == "stale"
    assert repo.heartbeat(worker, now=132)
    assert repo.worker_status(now=133)["status"] == "running"
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), repo.db.write() as conn:
        conn.execute(
            text(
                "INSERT INTO events(operation_id,type,payload_json,created_at) "
                "VALUES ('missing','bad','{}',100)"
            )
        )


async def test_graceful_stop_finishes_current_operation(setup_queue):
    settings, repo = setup_queue
    settings.worker_shutdown_seconds = 2
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(payload):
        entered.set()
        await release.wait()
        return {"finished": True}

    item = repo.enqueue("controlled", {})
    stop = asyncio.Event()
    task = asyncio.create_task(Worker(settings, {"controlled": handler}).run(stop))
    await asyncio.wait_for(entered.wait(), 5)
    stop.set()
    release.set()
    await asyncio.wait_for(task, 5)
    assert repo.get(item["id"])["status"] == "succeeded"
    assert repo.worker_status()["status"] == "stopped"
