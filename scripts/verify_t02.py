"""Run browser checks against an isolated migrated database and real Worker."""

import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from agentbuddy.config import Settings
from agentbuddy.persistence.database import Database
from agentbuddy.persistence.migrate import upgrade
from agentbuddy.persistence.repository import QueueRepository

ROOT = Path(__file__).resolve().parents[1]
PYTHON = (
    ROOT
    / "backend"
    / ".venv"
    / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
)
PNPM = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")


@contextmanager
def isolated_directory():
    folder = Path(tempfile.mkdtemp(prefix="agentbuddy-t02-")).resolve()
    try:
        yield str(folder)
    finally:
        # Only remove the directory allocated by this test. Windows can briefly
        # retain handles after the child services exit; retry cleanup, not jobs.
        assert folder.parent == Path(tempfile.gettempdir()).resolve()
        assert folder.name.startswith("agentbuddy-t02-")
        for attempt in range(20):
            try:
                shutil.rmtree(folder)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.25)


def main():
    with isolated_directory() as folder:
        settings = Settings(data_dir=Path(folder))
        settings.prepare_directories()
        upgrade(settings.database_path)
        db = Database(settings.database_path)
        repo = QueueRepository(db)
        env = {**os.environ, "DATA_DIR": folder, "T02_E2E": "1"}
        child = subprocess.Popen(
            [str(PYTHON), "-m", "agentbuddy.worker"],
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 15
            while repo.worker_status()["status"] != "running":
                if child.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Test Worker failed to start")
                time.sleep(0.05)
            for page_prefix, api_prefix in [
                ("/app", "/api/v1"),
                ("/workspace", "/custom/api/v1"),
            ]:
                test_env = {
                    **env,
                    "APP_BASE_PATH": page_prefix,
                    "API_PREFIX": api_prefix,
                }
                subprocess.run(
                    [PNPM, "--dir", "frontend", "test:e2e"],
                    cwd=ROOT,
                    env=test_env,
                    check=True,
                    timeout=120,
                )
        finally:
            child.terminate()
            child.wait(timeout=10)
            db.close()


if __name__ == "__main__":
    main()
