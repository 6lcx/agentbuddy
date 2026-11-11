"""Isolated T03/T04 native-protocol + browser integration; no real API keys."""

import os
import subprocess
import time

import httpx
from agentbuddy.config import Settings
from agentbuddy.persistence.migrate import upgrade
from verify_t02 import PNPM, PYTHON, ROOT, isolated_directory


def main():
    with isolated_directory() as folder:
        settings = Settings(data_dir=folder)
        settings.prepare_directories()
        upgrade(settings.database_path)
        env = {**os.environ, "DATA_DIR": folder, "T02_E2E": "1", "T04_E2E": "1"}
        children = []
        try:
            children.append(
                subprocess.Popen(
                    [
                        str(PYTHON),
                        "-m",
                        "uvicorn",
                        "provider_fixture:app",
                        "--app-dir",
                        str(ROOT / "backend/tests"),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8099",
                    ],
                    env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            )
            deadline = time.monotonic() + 15
            while True:
                try:
                    if (
                        httpx.get("http://127.0.0.1:8099/openapi.json").status_code
                        == 200
                    ):
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline or children[0].poll() is not None:
                    raise RuntimeError("Local protocol fixture failed to start")
                time.sleep(0.1)
            children.append(
                subprocess.Popen(
                    [str(PYTHON), "-m", "agentbuddy.worker"],
                    env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            )
            subprocess.run(
                [PNPM, "--dir", "frontend", "test:e2e"],
                cwd=ROOT,
                env=env,
                check=True,
                timeout=180,
            )
        finally:
            for child in reversed(children):
                child.terminate()
                child.wait(timeout=10)


if __name__ == "__main__":
    main()
