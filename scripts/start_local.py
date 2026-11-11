"""Start the local API, Worker and Vite UI; use your own model credentials."""

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx
from agentbuddy.config import Settings
from agentbuddy.persistence.migrate import upgrade

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--check", action="store_true", help="Start, check readiness, then stop."
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    settings = Settings()
    app_prefix = os.environ.get("APP_BASE_PATH", "/app").rstrip("/")
    if not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", app_prefix):
        parser.error("Invalid APP_BASE_PATH")
    if (
        app_prefix == settings.api_prefix
        or app_prefix.startswith(settings.api_prefix + "/")
        or settings.api_prefix.startswith(app_prefix + "/")
    ):
        parser.error("APP_BASE_PATH and API_PREFIX must not overlap")
    if args.api_port == args.web_port:
        parser.error("API and web ports must differ")
    for port in (args.api_port, args.web_port):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                parser.error(
                    f"Port {port} is occupied; existing services have not been stopped."
                )
    node = shutil.which("node")
    vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
    if not node or not vite.is_file():
        parser.error(
            "Install Node.js and run: pnpm --dir frontend install --frozen-lockfile"
        )
    settings.prepare_directories()
    upgrade(settings.database_path)
    log_dir = settings.data_dir / "logs" / time.strftime("local-%Y%m%d-%H%M%S")
    log_dir.mkdir(parents=True, exist_ok=True)
    api_url = f"http://127.0.0.1:{args.api_port}{settings.api_prefix}"
    ui_url = f"http://127.0.0.1:{args.web_port}{app_prefix}/"
    env = {
        **os.environ,
        "DATA_DIR": str(settings.data_dir),
        "API_PREFIX": settings.api_prefix,
        "APP_BASE_PATH": app_prefix,
        "API_PROXY_TARGET": f"http://127.0.0.1:{args.api_port}",
    }
    children, logs = [], []

    def start(name, command, cwd=ROOT):
        log = (log_dir / f"{name}.log").open("w", encoding="utf-8")
        logs.append(log)
        child = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        children.append(child)

    def ready(url, worker=False):
        with httpx.Client(timeout=2, trust_env=False) as client:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError(f"A service exited. Check {log_dir}")
                try:
                    response = client.get(url)
                    if response.status_code == 200 and (
                        not worker or response.json()["worker"]["status"] == "running"
                    ):
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
        raise RuntimeError(f"Service readiness timed out. Check {log_dir}")

    try:
        start(
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agentbuddy.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.api_port),
            ],
        )
        ready(api_url + "/health")
        start("worker", [sys.executable, "-m", "agentbuddy.worker"])
        start(
            "web",
            [node, str(vite), "--host", "127.0.0.1", "--port", str(args.web_port)],
            ROOT / "frontend",
        )
        ready(ui_url)
        ready(api_url + "/system/status", worker=True)
        print(
            f"AgentBuddy: {ui_url}\nLogs: {log_dir}\nConfigure your own model in Settings. No shared API key is included.",
            flush=True,
        )
        if args.check:
            time.sleep(2)
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A service exited during startup check.")
            print("Startup check passed.", flush=True)
            return
        if not args.no_browser:
            webbrowser.open(ui_url)
        print(
            "Keep this terminal open. Finish or cancel active tasks before Ctrl+C.",
            flush=True,
        )
        while True:
            if any(child.poll() is not None for child in children):
                raise RuntimeError(f"A service exited. Check {log_dir}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping local services...")
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
