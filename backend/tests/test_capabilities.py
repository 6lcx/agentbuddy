import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import test_chat
from fastapi.testclient import TestClient
from test_chat import configure

from agentbuddy.catalog.capabilities import Capabilities
from agentbuddy.catalog.service import DomainError
from agentbuddy.main import create_app
from agentbuddy.persistence.repository import QueueRepository
from agentbuddy.providers.native import NativeModel, ProviderFailure
from agentbuddy.runtime.control import Control
from agentbuddy.runtime.files import Files, manifest, unpack
from agentbuddy.runtime.repository import RunRepository
from agentbuddy.runtime.tools import RunTools, discover, mcp_session


@pytest.fixture
def setup_chat(tmp_path):
    yield from test_chat.setup_chat.__wrapped__(tmp_path)


def package(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


async def running(db, payload=None):
    await configure(db)
    repo = RunRepository(db)
    created = repo.submit({"message": "test", **(payload or {})}, os.urandom(8).hex())
    worker = QueueRepository(db).start_worker()
    return repo, repo.claim(worker), created


async def test_atomic_last_budget_cancel_late_completion(setup_chat):
    _, db = setup_chat
    repo, claimed, _ = await running(db, {"calls_limit": 1})
    control = Control(db, claimed["id"])

    def reserve(_):
        try:
            control.check(reserve=True)
            return True
        except ProviderFailure:
            return False

    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(reserve, range(8))) == 1
    assert control.cancel()
    assert not repo.finish(claimed, text="late")
    assert not repo.append(claimed, "late")


async def test_confirmation_digest_reject_pauses_clock(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db)
    control = Control(db, claimed["id"])
    task = asyncio.create_task(control.confirm({"target": "somewhere", "body": "exact body"}))
    await asyncio.sleep(0.05)
    pending = control.pending()[0]
    assert control.state()["active_since"] is None
    elapsed = control.state()["elapsed"]
    with pytest.raises(DomainError):
        control.answer(pending["id"], "wrong", True)
    control.answer(pending["id"], pending["digest"], False)
    assert await task is False
    assert control.state()["elapsed"] == elapsed
    assert control.state()["active_since"] is not None


@pytest.mark.parametrize(
    "entries", [{"../escape": "bad"}, {"a": "x", "a/b": "y"}, {"A.txt": "x", "a.txt": "y"}]
)
def test_zip_rejects_conflicts(tmp_path, entries):
    with pytest.raises(DomainError):
        unpack(package(entries), tmp_path)


async def test_skill_real_script_and_versions_preserve_inputs(setup_chat):
    settings, db = setup_chat
    registry = Capabilities(db)
    skill = registry.install(
        package(
            {
                "skill.json": json.dumps({"name": "writer", "scripts": ["write.py"]}),
                "SKILL.md": "Create report.txt",
                "write.py": "from pathlib import Path\nPath('report.txt').write_text('v1')",
            }
        )
    )
    assert not (db.path.parent / "report.txt").exists()
    assert not skill["config_json"]["enabled"]
    skill = registry.save("skill", {**skill["config_json"], "enabled": True}, skill["id"], 1)
    files = Files(db)
    upload = files.upload("input.txt", b"original", "draft")
    original = manifest(db.path.parent / "uploads" / upload["id"])
    repo, claimed, created = await running(db, {"upload_ids": [upload["id"]], "draft_id": "draft"})
    root = files.workspace(claimed)
    tools = RunTools(db, claimed, Control(db, claimed["id"]), root)
    with pytest.raises(DomainError):
        await tools.run_script(skill["id"], "write.py", [])
    await tools.load_skill(skill["id"])
    result = await tools.run_script(skill["id"], "write.py", [])
    assert json.loads(result.content[0].text)["exit_code"] == 0
    with TestClient(create_app(settings)) as client:
        assert (
            client.get("/api/v1" + json.loads(result.content[0].text)["log_url"]).status_code == 200
        )
    version = files.stage_version(claimed, root)
    assert repo.finish(claimed, text="done", version=version)
    first_bytes = files.archive(version["id"])
    second = repo.submit({"project_id": created["project_id"], "message": "edit"}, "second")
    claimed2 = repo.claim(claimed["worker_id"])
    root2 = files.workspace(claimed2)
    (root2 / "report.txt").write_text("v2", encoding="utf-8")
    version2 = files.stage_version(claimed2, root2)
    assert repo.finish(claimed2, text="v2", version=version2)
    assert first_bytes == files.archive(version["id"])
    assert manifest(db.path.parent / "uploads" / upload["id"]) == original
    assert second["run_id"] != created["run_id"]
    repo.submit({"project_id": created["project_id"], "message": "failed edit"}, "third")
    third = repo.claim(claimed["worker_id"])
    (files.workspace(third) / "report.txt").write_text("failed", encoding="utf-8")
    staged = files.stage_version(third, files.workspace(third))
    Control(db, third["id"]).cancel()
    assert not repo.finish(third, text="late", version=staged)
    assert [a for a in files.list(created["project_id"]) if a["kind"] == "version"][-1][
        "id"
    ] == version2["id"]


async def test_expert_manual_overrides_and_run_snapshot(setup_chat):
    _, db = setup_chat
    registry = Capabilities(db)
    ids = {name: registry.save("skill", {"name": name, "enabled": True})["id"] for name in "XYZW"}
    a = registry.save("expert", {"name": "A", "skill_ids": [ids["X"], ids["Z"]]})
    b = registry.save("expert", {"name": "B", "skill_ids": [ids["X"], ids["W"]]})
    selection = {"expert_id": a["id"], "add_skills": [ids["Y"]], "remove_skills": [ids["X"]]}
    repo, claimed, _ = await running(db, {"selection": selection})
    assert {
        s["config_json"]["name"] for s in claimed["snapshot_json"]["capabilities"]["skills"]
    } == {"Z", "Y"}
    selection["expert_id"] = b["id"]
    assert {s["config_json"]["name"] for s in registry.resolve(selection)["skills"]} == {"W", "Y"}
    assert registry.get(a["id"])["config_json"]["skill_ids"] == [ids["X"], ids["Z"]]
    assert repo.get(claimed["id"])["snapshot_json"]["capabilities"]["expert"]["id"] == a["id"]


async def test_mcp_real_stdio_and_rejection_never_connects(setup_chat):
    _, db = setup_chat
    config = {
        "name": "fixture",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(Path(__file__).with_name("mcp_fixture.py"))],
        "enabled": True,
    }
    assert (await discover(config))[0]["name"] == "echo"
    async with mcp_session(config) as session:
        result = await session.call_tool("echo", {"text": "actual MCP"})
        assert result.content[0].text == "actual MCP"
    registry = Capabilities(db)
    connector = registry.save("connector", {**config, "command": "does-not-exist"})
    expert = registry.save("expert", {"name": "test", "connector_ids": [connector["id"]]})
    _, claimed, _ = await running(db, {"selection": {"expert_id": expert["id"]}})
    control = Control(db, claimed["id"])
    tools = RunTools(db, claimed, control, Files(db).workspace(claimed))
    call = asyncio.create_task(tools.call_connector(connector["id"], "echo", {"text": "no"}))
    await asyncio.sleep(0.05)
    pending = control.pending()[0]
    control.answer(pending["id"], pending["digest"], False)
    assert json.loads((await call).content[0].text)["executed"] is False
    assert control.state()["calls_used"] == 0


async def test_mcp_real_streamable_http():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("mcp_fixture.py"))],
        env={**os.environ, "MCP_TEST_PORT": str(port), "MCP_TEST_TRANSPORT": "streamable-http"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                try:
                    await client.get(f"http://127.0.0.1:{port}/mcp")
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)
        assert (await discover({"transport": "http", "url": f"http://127.0.0.1:{port}/mcp"}))[0][
            "name"
        ] == "echo"
    finally:
        process.terminate()
        process.wait(timeout=10)


async def test_run_model_retries_share_budget(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db, {"calls_limit": 2})
    attempts = []

    def fail(request):
        attempts.append(1)
        return httpx.Response(429)

    model = NativeModel(claimed["snapshot_json"], "local-test-key", httpx.MockTransport(fail))
    model.control = Control(db, claimed["id"])
    try:
        with pytest.raises(ProviderFailure, match="调用次数"):
            async for _ in await model._call_api("test", []):
                pass
        assert len(attempts) == 2
    finally:
        await model.close()


async def test_upload_and_prefixed_download_api(setup_chat):
    import base64

    settings, db = setup_chat
    await configure(db)
    from agentbuddy.worker.service import Worker

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/uploads",
            json={
                "name": "a.txt",
                "content_base64": base64.b64encode(b"actual").decode(),
                "draft_id": "d",
            },
        )
        assert response.status_code == 202
        operation_id = response.json()["data"]["id"]
        await Worker(settings).run(once=True)
        identifier = client.get(f"/api/v1/operations/{operation_id}").json()["result_json"]["id"]
        created = RunRepository(db).submit({"message": "cancel"}, "cancel")
        result = client.get(f"/api/v1/files/{identifier}/download")
        assert zipfile.ZipFile(io.BytesIO(result.content)).read("a.txt") == b"actual"
        assert client.post(f"/api/v1/runs/{created['run_id']}/cancel", json={}).json()["data"][
            "cancelled"
        ]


@pytest.mark.parametrize("kind", ["qwen_dashscope", "deepseek", "claude"])
async def test_agentscope_actual_file_tool_and_published_version(setup_chat, kind):
    from agentbuddy.worker.service import Worker

    settings, db = setup_chat
    _, model, factory = await configure(db, kind)
    repo = RunRepository(db)
    created = repo.submit({"message": "GENERATE_TEST_FILE", "model_id": model["id"]}, "generate")
    worker = Worker(settings)
    worker.executor.factory = factory
    await worker.run(once=True)
    assert repo.get(created["run_id"])["status"] == "succeeded"
    versions = Files(db).list(created["project_id"])
    assert len(versions) == 1
    assert (
        zipfile.ZipFile(io.BytesIO(Files(db).archive(versions[0]["id"]))).read("report.txt")
        == b"verified project file"
    )
    assert Control(db, created["run_id"]).state()["calls_used"] == 4


async def test_skill_versions_dependencies_and_uploaded_mcp(setup_chat):
    _, db = setup_chat
    registry = Capabilities(db)
    with pytest.raises(DomainError):
        registry.install(package({"SKILL.md": "missing manifest"}))
    with pytest.raises(DomainError, match="服务器缺少"):
        registry.install(
            package(
                {
                    "SKILL.md": "test",
                    "skill.json": json.dumps(
                        {"name": "missing", "requires": ["agentbuddy_nonexistent_dependency"]}
                    ),
                }
            )
        )
    one = registry.install(
        package(
            {
                "SKILL.md": "first version",
                "skill.json": json.dumps({"name": "versioned", "version": "1"}),
            }
        )
    )
    two = registry.install(
        package(
            {
                "SKILL.md": "second version",
                "skill.json": json.dumps({"name": "versioned", "version": "2"}),
            }
        ),
        one["id"],
    )
    assert two["revision"] == 2
    old = registry.list("skill_version")[0]["config_json"]["package"]
    assert old["instructions"] == "first version"
    assert (db.path.parent / "skills" / old["package_id"] / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "first version"
    connector = registry.import_connector(
        package(
            {
                "mcp.json": json.dumps({"name": "uploaded MCP", "entry": "server.py"}),
                "server.py": Path(__file__).with_name("mcp_fixture.py").read_text(encoding="utf-8"),
            }
        )
    )
    assert (await discover(connector["config_json"]))[0]["name"] == "echo"


async def test_required_connector_waits_and_unrelated_failure_does_not_block(setup_chat):
    settings, db = setup_chat
    from agentbuddy.worker.service import Worker

    _, _, factory = await configure(db)
    registry = Capabilities(db)
    connector = registry.save(
        "connector",
        {
            "name": "unavailable",
            "transport": "stdio",
            "command": "agentbuddy-no-such-command",
            "enabled": True,
            "effects": {"echo": "read"},
        },
    )
    expert = registry.save(
        "expert", {"name": "test", "enabled": True, "connector_ids": [connector["id"]]}
    )
    repo = RunRepository(db)
    first = repo.submit(
        {"message": "ordinary question", "selection": {"expert_id": expert["id"]}}, "unrelated"
    )
    worker = Worker(settings)
    worker.executor.factory = factory
    await worker.run(once=True)
    assert repo.get(first["run_id"])["status"] == "succeeded"
    second = repo.submit(
        {"message": "use connector", "selection": {"expert_id": expert["id"]}}, "required"
    )
    worker_id = QueueRepository(db).start_worker()
    claimed = repo.claim(worker_id)
    control = Control(db, second["run_id"])
    tools = RunTools(db, claimed, control, Files(db).workspace(claimed))
    task = asyncio.create_task(tools.call_connector(connector["id"], "echo", {"text": "hello"}))
    for _ in range(100):
        if control.pending():
            break
        await asyncio.sleep(0.02)
    pending = control.pending()[0]
    assert pending["payload_json"]["kind"] == "dependency"
    assert repo.public(repo.get(second["run_id"]))["status"] == "waiting_input"
    control.answer(pending["id"], pending["digest"], False)
    assert json.loads((await task).content[0].text)["executed"] is False


async def test_upload_only_question_has_no_version_and_expiry_preserves_project(setup_chat):
    _, db = setup_chat
    files = Files(db)
    attached = files.upload("input.txt", b"original", "draft")
    _, claimed, _ = await running(db, {"upload_ids": [attached["id"]], "draft_id": "draft"})
    root = files.workspace(claimed)
    assert files.stage_version(claimed, root) is None
    orphan = files.upload("orphan.txt", b"old", "other")
    assert files.expire_drafts(orphan["created_at"] + 86401) == 1
    assert (db.path.parent / "uploads" / attached["id"]).exists()
    assert not (db.path.parent / "uploads" / orphan["id"]).exists()


async def test_clarification_and_cancel_inflight_tool(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db)
    control = Control(db, claimed["id"])
    tools = RunTools(db, claimed, control, Files(db).workspace(claimed))
    task = asyncio.create_task(tools.ask_user("Which output file?"))
    await asyncio.sleep(0.05)
    interaction = control.pending()[0]
    control.answer(interaction["id"], interaction["digest"], True, "report.md")
    assert json.loads((await task).content[0].text)["answer"] == "report.md"
    ended = asyncio.Event()

    async def long_tool():
        try:
            await asyncio.sleep(60)
        finally:
            ended.set()

    guarded = asyncio.create_task(control.guard(long_tool()))
    await asyncio.sleep(0.05)
    control.cancel()
    with pytest.raises(ProviderFailure):
        await guarded
    assert ended.is_set()
    assert control.state()["active_since"] is None


async def test_project_selection_persists_without_changing_run(setup_chat):
    settings, db = setup_chat
    repo, claimed, created = await running(db)
    with TestClient(create_app(settings)) as client:
        url = f"/api/v1/projects/{created['project_id']}/selection"
        before = client.get(url).json()["data"]
        selected = {"add_skills": [], "remove_skills": [], "add_connectors": []}
        assert (
            client.patch(
                url, json={"selection": selected, "revision": before["revision"]}
            ).status_code
            == 200
        )
        assert client.get(url).json()["data"]["selection"] == selected
        assert (
            client.patch(
                url, json={"selection": selected, "revision": before["revision"]}
            ).status_code
            == 409
        )
        assert repo.get(claimed["id"])["snapshot_json"]["capabilities"]["selection"] == {}
        assert client.get("/api/v1/runs/missing/control").status_code == 404


@pytest.mark.parametrize("phase", ["copy", "rename"])
async def test_publish_failure_keeps_previous_version(setup_chat, monkeypatch, phase):
    _, db = setup_chat
    repo, claimed, created = await running(db)
    files = Files(db)
    root = files.workspace(claimed)
    (root / "a.txt").write_text("v1", encoding="utf-8")
    first = files.stage_version(claimed, root)
    repo.finish(claimed, text="v1", version=first)
    old = files.archive(first["id"])
    repo.submit({"project_id": created["project_id"], "message": "v2"}, "publication-v2")
    second = repo.claim(claimed["worker_id"])
    next_root = files.workspace(second)
    (next_root / "a.txt").write_text("v2", encoding="utf-8")
    import agentbuddy.runtime.files as file_module

    def crash(*args, **kwargs):
        raise OSError("injected publication failure")

    monkeypatch.setattr(
        file_module.shutil if phase == "copy" else file_module.os,
        "copytree" if phase == "copy" else "replace",
        crash,
    )
    with pytest.raises(OSError):
        files.stage_version(second, next_root)
    repo.interrupt(second["worker_id"])
    assert files.archive(first["id"]) == old
    assert len([a for a in files.list(created["project_id"]) if a["kind"] == "version"]) == 1


async def test_deleting_last_file_publishes_empty_version(setup_chat):
    _, db = setup_chat
    repo, claimed, _ = await running(db)
    files = Files(db)
    root = files.workspace(claimed)
    (root / "last.txt").write_text("last")
    version = files.stage_version(claimed, root)
    repo.finish(claimed, text="created", version=version)
    original = files.archive(version["id"])
    submitted = repo.submit(
        {"message": "delete", "project_id": claimed["project_id"]}, "delete-last"
    )
    next_run = repo.claim(claimed["worker_id"])
    assert next_run["id"] == submitted["run_id"]
    next_root = files.workspace(next_run)
    (next_root / "last.txt").unlink()
    empty = files.stage_version(next_run, next_root)
    assert empty is not None and empty["manifest_json"] == []
    repo.finish(next_run, text="deleted", version=empty)
    assert files.archive(version["id"]) == original
    assert len(files.list(claimed["project_id"])) == 2


async def test_deleting_all_uploaded_files_publishes_empty_first_version(setup_chat):
    _, db = setup_chat
    files = Files(db)
    asset = files.upload("only.txt", b"original", "delete-upload")
    repo, claimed, _ = await running(db, {"upload_ids": [asset["id"]], "draft_id": "delete-upload"})
    root = files.workspace(claimed)
    (root / "only.txt").unlink()
    version = files.stage_version(claimed, root)
    assert version is not None and version["manifest_json"] == []
    repo.finish(claimed, text="deleted", version=version)
    assert (db.path.parent / "uploads" / asset["id"] / "only.txt").read_bytes() == b"original"
