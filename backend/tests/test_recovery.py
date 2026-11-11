"""Actual process termination and recovery of a claimed run and its children."""

import os
import subprocess
import sys
import time

import pytest
import test_chat
from sqlalchemy import select
from test_chat import configure

from agentbuddy.persistence.schema import subtasks
from agentbuddy.runtime.repository import RunRepository


@pytest.fixture
def setup_chat(tmp_path):
    yield from test_chat.setup_chat.__wrapped__(tmp_path)


async def test_crashed_run_retains_partial_files_and_never_replays(setup_chat):
    settings, db = setup_chat
    await configure(db)
    repo = RunRepository(db)
    accepted = repo.submit({"message": "long running"}, "crash-run")
    code = """
import time
from filelock import FileLock
from sqlalchemy import insert
from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.persistence.repository import QueueRepository
from agentbuddy.persistence.schema import subtasks
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.runtime.control import Control
from agentbuddy.runtime.files import Files
settings=Settings()
db=Database(settings.database_path)
with FileLock(str(settings.database_path)+'.worker.lock'):
 worker=QueueRepository(db).start_worker()
 run=RunRepository(db).claim(worker)
 Control(db,run['id']).lane('active')
 with db.write() as conn:
  conn.execute(insert(subtasks).values(id='child',run_id=run['id'],task_key='child',expert_id='fixture',status='running',plan_json={}))
 root=Files(db).workspace(run)
 (root/'partial.txt').write_text('retained partial')
 (settings.data_dir/'started.marker').write_text('one execution')
 time.sleep(100)
"""
    env = {**os.environ, "DATA_DIR": str(settings.data_dir)}
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 15
        while not (settings.data_dir / "started.marker").exists():
            assert child.poll() is None
            if time.monotonic() > deadline:
                raise AssertionError("Child did not start")
            await __import__("asyncio").sleep(0.05)
    finally:
        child.kill()
        child.wait(timeout=10)
    completed = subprocess.run(
        [sys.executable, "-m", "agentbuddy.worker", "--once"],
        env=env,
        capture_output=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    run = repo.get(accepted["run_id"])
    assert run["status"] == "interrupted"
    with db.engine.connect() as conn:
        assert (
            conn.scalar(select(subtasks.c.status).where(subtasks.c.id == "child")) == "interrupted"
        )
    path = (
        settings.data_dir
        / "projects"
        / accepted["project_id"]
        / "attempts"
        / accepted["run_id"]
        / "partial.txt"
    )
    assert path.read_text() == "retained partial"
    assert (settings.data_dir / "started.marker").read_text() == "one execution"


@pytest.mark.skipif(os.name != "nt", reason="Windows job object; Linux service uses cgroup")
def test_windows_worker_tree_exits_when_owner_is_killed(tmp_path):
    import ctypes
    from ctypes import wintypes

    marker = tmp_path / "child.pid"
    code = """
import subprocess,sys,time
from pathlib import Path
from agentbuddy.worker.process_tree import contain_process_tree
contain_process_tree()
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(100)'])
Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(100)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", code, str(marker)], creationflags=subprocess.CREATE_NO_WINDOW
    )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = None
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert parent.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.05)
        handle = kernel.OpenProcess(0x100000, False, int(marker.read_text()))
        assert handle
        parent.kill()
        parent.wait(timeout=5)
        assert kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if handle:
            kernel.CloseHandle(handle)


async def test_os_kill_at_checkpoint_resumes_same_run(setup_chat):
    from pathlib import Path

    from agentbuddy.persistence.repository import QueueRepository
    from agentbuddy.runtime.checkpoints import resume_run
    from agentbuddy.runtime.control import Control
    from agentbuddy.runtime.executor import Executor
    from agentbuddy.runtime.runtime_store import RuntimeStore

    settings, db = setup_chat
    _, _, factory = await configure(db)
    repo = RunRepository(db)
    accepted = repo.submit({"message": "hello after recovery"}, "safe-crash")
    code = r"""
import asyncio,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_native_providers import fixture_transport
from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.persistence.repository import QueueRepository
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.runtime.executor import Executor
from agentbuddy.runtime.context_runtime import ContextRuntime
from agentbuddy.providers.native import NativeModel
settings=Settings()
db=Database(settings.database_path)
original=ContextRuntime.on_model_call
async def wait_after_checkpoint(self, agent, input_kwargs, next_handler):
    await original(self, agent, input_kwargs, next_handler)
    (settings.data_dir/'safe.marker').write_text('saved')
    await asyncio.sleep(100)
ContextRuntime.on_model_call=wait_after_checkpoint
worker=QueueRepository(db).start_worker()
run=RunRepository(db).claim(worker)
def factory(snapshot,secret):
    return NativeModel(snapshot,secret,fixture_transport(snapshot['provider']['provider_type'],[]))
asyncio.run(Executor(db,factory).execute(run))
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(Path(__file__).parent)],
        env={**os.environ, "DATA_DIR": str(settings.data_dir)},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 20
        while not (settings.data_dir / "safe.marker").exists():
            assert child.poll() is None
            assert time.monotonic() < deadline
            await __import__("asyncio").sleep(0.05)
    finally:
        child.kill()
        child.wait(timeout=10)
    run_id = accepted["run_id"]
    store = RuntimeStore(db, accepted["project_id"], run_id)
    assert store.get("checkpoint")["safe"]
    used = Control(db, run_id).state()["calls_used"]
    repo.interrupt()
    ledger = settings.data_dir / "agent_budgets" / run_id / "main.json"
    import json

    assert json.loads(ledger.read_text())["active_since"] is None
    resume_run(db, repo.get(run_id))
    assert Control(db, run_id).state()["calls_used"] == used
    await Executor(db, factory).execute(repo.claim(QueueRepository(db).start_worker()))
    assert repo.get(run_id)["status"] == "succeeded"
