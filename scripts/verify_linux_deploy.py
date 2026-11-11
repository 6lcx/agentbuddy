"""Linux deployment smoke: nginx static fallback, real API/Worker and unbuffered SSE."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from agentbuddy.config import Settings
from render_nginx import render
from verify_deepseek import configure


def port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def ready(url):
    until = time.monotonic() + 20
    while time.monotonic() < until:
        try:
            if httpx.get(url, timeout=1, trust_env=False).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Server not ready: " + url)


async def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend-dist", type=Path, required=True)
    parser.add_argument("--tests-dir", type=Path, required=True)
    args = parser.parse_args()
    assert os.name == "posix"
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    with tempfile.TemporaryDirectory(prefix="agentbuddy-linux-deploy-") as folder:
        root = Path(folder)
        fixture_port, api_port, web_port = port(), port(), port()
        processes = []
        logs = []

        def launch(command, env=None):
            log = (root / f"process-{len(logs)}.log").open("wb")
            logs.append(log)
            child = subprocess.Popen(
                command, env=env, stdout=log, stderr=subprocess.STDOUT
            )
            processes.append(child)
            return child

        db = None
        try:
            launch(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "provider_fixture:app",
                    "--app-dir",
                    str(args.tests_dir),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(fixture_port),
                ]
            )
            ready(f"http://127.0.0.1:{fixture_port}/docs")
            settings = Settings(data_dir=root / "data")
            db, _model = await configure(
                settings,
                {
                    "LLM_BASE_URL": f"http://127.0.0.1:{fixture_port}/deepseek",
                    "LLM_API_KEY": "local-test-key",
                    "LLM_MODEL": "local-fixture",
                },
            )
            env = {
                **os.environ,
                "DATA_DIR": str(settings.data_dir),
                "API_PREFIX": "/api/v1",
            }
            launch([sys.executable, "-m", "agentbuddy.worker"], env)
            launch(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "agentbuddy.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                ],
                env,
            )
            ready(f"http://127.0.0.1:{api_port}/api/v1/health")
            shutil.copytree(args.frontend_dist, root / "www/app")
            (root / "logs").mkdir()
            config = root / "nginx.conf"
            config.write_text(
                render(root / "www", port=web_port, backend_port=api_port)
            )
            launch(
                [
                    "/usr/sbin/nginx",
                    "-p",
                    str(root) + "/",
                    "-c",
                    str(config),
                    "-g",
                    "daemon off;",
                ]
            )
            url = f"http://127.0.0.1:{web_port}"
            ready(url + "/api/v1/health")
            async with httpx.AsyncClient(
                base_url=url, trust_env=False, timeout=30
            ) as client:
                assert (
                    await client.get("/app/projects/refresh-test")
                ).status_code == 200
                assert "root" in (await client.get("/app/")).text
                status = (await client.get("/api/v1/system/status")).json()
                assert status["worker"]["status"] == "running"
                response = await client.post(
                    "/api/v1/runs",
                    json={"message": "slow GENERATE_TEST_FILE"},
                    headers={"Idempotency-Key": "nginx-smoke"},
                )
                response.raise_for_status()
                run = response.json()["data"]
                started = time.monotonic()
                first = None
                seen = []
                async with client.stream(
                    "GET", f"/api/v1/runs/{run['run_id']}/events"
                ) as events:
                    assert events.status_code == 200
                    async for line in events.aiter_lines():
                        if line.startswith("data:"):
                            first = first or time.monotonic() - started
                            seen.append(json.loads(line[5:])["type"])
                assert first < 2 and "run.completed" in seen
                assert (await client.get(f"/api/v1/runs/{run['run_id']}")).json()[
                    "data"
                ]["status"] == "succeeded"
                versions = (
                    await client.get(f"/api/v1/projects/{run['project_id']}/files")
                ).json()["data"]
                file = await client.get(
                    f"/api/v1/files/{versions[-1]['id']}/content",
                    params={"path": "report.txt"},
                )
                assert file.text == "verified project file"
                assert (
                    await client.get(f"/api/v1/files/{versions[-1]['id']}/download")
                ).headers["content-type"] == "application/zip"
                print(
                    json.dumps(
                        {
                            "linux_nginx_api_worker": "passed",
                            "sse_first_seconds": round(first, 3),
                            "static_nested_route": True,
                            "file_download": True,
                            "office_renderer": status["office_renderer_available"],
                        }
                    )
                )
        except Exception:
            for log in logs:
                log.flush()
                print(
                    Path(log.name).read_text(errors="replace")[-3000:], file=sys.stderr
                )
            raise
        finally:
            for child in reversed(processes):
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            if db:
                db.close()
            for log in logs:
                log.close()


if __name__ == "__main__":
    asyncio.run(main())
