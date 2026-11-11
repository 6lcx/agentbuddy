import asyncio
from types import SimpleNamespace

import pytest
import test_chat
from agentscope.message import Msg, TextBlock

from agentbuddy.providers.native import ProviderFailure
from agentbuddy.runtime.context_policy import ContextPolicy
from agentbuddy.runtime.context_runtime import ContextRuntime
from agentbuddy.runtime.runtime_store import RuntimeStore


@pytest.fixture
def setup_chat(tmp_path):
    yield from test_chat.setup_chat.__wrapped__(tmp_path)


def test_result_roundtrip_ranges_integrity_and_quota(setup_chat):
    settings, db = setup_chat
    store = RuntimeStore(db, "project", "run")
    policy = ContextPolicy(result_max_bytes=65536, result_quota_bytes=65536)
    ref = store.result("a" * 40000, policy)
    assert store.read(ref["result_id"], 39990)["text"] == "a" * 10
    assert store.result("a" * 40000, policy) == ref
    chinese = "中文分页末尾" * 40
    unicode_ref = store.result(chinese, policy)
    offset, pages = 0, []
    while True:
        page = store.read(unicode_ref["result_id"], offset, 17)
        pages.append(page["text"])
        offset = page["next_offset"]
        if page["eof"]:
            break
    assert "".join(pages) == chinese
    with pytest.raises(ProviderFailure, match="额度"):
        RuntimeStore(db, "project", "run", "child").result("b" * 40000, policy)
    with pytest.raises(ValueError):
        store.read("../other")
    (store.root / (ref["result_id"] + ".txt")).write_text("changed")
    with pytest.raises(ProviderFailure, match="完整性"):
        store.read(ref["result_id"])


async def test_checkpoint_resume_preserves_budget_and_state(setup_chat, monkeypatch):
    from agentbuddy.persistence.repository import QueueRepository
    from agentbuddy.runtime.checkpoints import resume_run
    from agentbuddy.runtime.control import Control
    from agentbuddy.runtime.executor import Executor
    from agentbuddy.runtime.repository import RunRepository

    settings, db = setup_chat
    _, _, factory = await test_chat.configure(db)
    repo = RunRepository(db)
    accepted = repo.submit({"message": "hello"}, "checkpoint")
    worker = QueueRepository(db).start_worker()
    claimed = repo.claim(worker)
    original = ContextRuntime.on_model_call

    async def crash(self, agent, input_kwargs, next_handler):
        await original(self, agent, input_kwargs, next_handler)
        raise asyncio.CancelledError()

    monkeypatch.setattr(ContextRuntime, "on_model_call", crash)
    with pytest.raises(asyncio.CancelledError):
        await Executor(db, factory).execute(claimed)
    used = Control(db, claimed["id"]).state()["calls_used"]
    repo.interrupt()
    from agentbuddy.catalog.service import DomainError

    store = RuntimeStore(db, claimed["project_id"], claimed["id"])
    checkpoint = store.get("checkpoint")
    store.put("checkpoint", {**checkpoint, "safe": False})
    with pytest.raises(DomainError, match="没有可安全恢复"):
        resume_run(db, repo.get(accepted["run_id"]))
    missing = store.result("checkpoint source", ContextPolicy())
    store.put("checkpoint", {**checkpoint, "context": {"source": missing}})
    (store.root / (missing["result_id"] + ".txt")).unlink()
    with pytest.raises(DomainError, match="结果引用缺失"):
        resume_run(db, repo.get(accepted["run_id"]))
    store.put("checkpoint", checkpoint)
    resume_run(db, repo.get(accepted["run_id"]))
    assert Control(db, claimed["id"]).state()["calls_used"] == used
    monkeypatch.setattr(ContextRuntime, "on_model_call", original)
    await Executor(db, factory).execute(repo.claim(worker))
    assert repo.get(claimed["id"])["status"] == "succeeded"


async def test_context_summary_reused_and_current_user_preserved(setup_chat):
    _, db = setup_chat
    store = RuntimeStore(db, "project", "run")
    events = []
    control = SimpleNamespace(event=lambda *args: events.append(args))
    tools = SimpleNamespace(store=store, control=control)
    runtime = ContextRuntime(tools, {"context_policy": {"reserve_tokens": 256}})

    class Model:
        context_size = 10000
        parameters = SimpleNamespace(max_tokens=512)
        calls = 0

        def encode(self, messages, tools=None, choice=None):
            return (
                "",
                {},
                {"messages": [m.model_dump(mode="json") for m in messages], "tools": tools},
            )

        async def __call__(self, messages):
            self.calls += 1

            async def stream():
                yield SimpleNamespace(content=[TextBlock(text="Budget 100. Source retained.")])

            return stream()

    model = Model()
    messages = [
        Msg(name="system", role="system", content=[TextBlock(text="stable")]),
        Msg(name="user", role="user", content=[TextBlock(text="x " * 2600)]),
        Msg(name="assistant", role="assistant", content=[TextBlock(text="y " * 1200)]),
        Msg(name="user", role="user", content=[TextBlock(text="keep current request")]),
    ]
    prepared = await runtime.prepare(model, messages, [], None)
    assert model.calls == 1
    assert prepared[0] == messages[0] and prepared[-1] == messages[-1]
    restored = ContextRuntime(tools, {"context_policy": {"reserve_tokens": 256}})
    again = await restored.prepare(model, messages, [], None)
    assert model.calls == 1
    from agentbuddy.providers.native import encode_messages

    assert encode_messages(again) == encode_messages(prepared)
    assert store.get("context")["source"]["complete"]


async def test_skill_and_connector_disclosure_is_lazy(setup_chat):
    from agentbuddy.catalog.service import Catalog
    from agentbuddy.runtime.executor import Executor
    from agentbuddy.runtime.tools import RunTools

    _, db = setup_chat
    _, model_row, factory = await test_chat.configure(db)
    snapshot = Catalog(db).test_payload(model_row["id"])
    snapshot["capabilities"] = {
        "connectors": [
            {
                "id": "connector",
                "revision": 1,
                "config_json": {
                    "name": "Search",
                    "tools": [
                        {
                            "name": "lookup",
                            "description": "Find records",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"private_schema_marker": {"type": "string"}},
                            },
                        }
                    ],
                },
            }
        ]
    }
    control = SimpleNamespace(check=lambda **kwargs: None, event=lambda *args: None)
    claimed = {"id": "run", "project_id": "project", "snapshot_json": snapshot}
    root = db.path.parent / "workspace"
    root.mkdir()
    tools = RunTools(db, claimed, control, root)
    model = factory(snapshot, "test")
    try:
        agent = Executor(db).make_agent(snapshot, model, tools)
        assert "private_schema_marker" not in agent._system_prompt
        listing = await tools.discover_tools("connector")
        assert "private_schema_marker" not in listing.content[0].text
        detail = await tools.describe_tool("connector", "lookup")
        assert "private_schema_marker" in detail.content[0].text
        repeat = await tools.describe_tool("connector", "lookup")
        assert '"already_loaded": true' in repeat.content[0].text
        with pytest.raises(Exception, match="本轮能力"):
            await tools.describe_tool("not-allowed", "lookup")
    finally:
        await model.close()


def test_safe_cuts_never_split_tool_pair():
    from agentscope.message import ToolCallBlock, ToolResultBlock

    from agentbuddy.runtime.context_runtime import safe_cuts

    messages = [
        Msg(name="user", role="user", content=[TextBlock(text="request")]),
        Msg(
            name="agent",
            role="assistant",
            content=[ToolCallBlock(id="call", name="read_file", input="{}")],
        ),
        Msg(
            name="tool",
            role="assistant",
            content=[
                ToolResultBlock(id="call", name="read_file", output=[TextBlock(text="result")])
            ],
        ),
    ]
    assert safe_cuts(messages) == [1, 3]


async def test_retention_pins_interrupted_results(setup_chat):
    import time

    from sqlalchemy import update

    from agentbuddy.persistence.schema import runs
    from agentbuddy.runtime.repository import RunRepository
    from agentbuddy.runtime.retention import expire_runtime

    _, db = setup_chat
    await test_chat.configure(db)
    repo = RunRepository(db)
    first = repo.submit({"message": "one"}, "retention-1")
    second = repo.submit({"message": "two"}, "retention-2")
    stores = [RuntimeStore(db, r["project_id"], r["run_id"]) for r in (first, second)]
    for store in stores:
        store.result("retained", ContextPolicy())
    with db.write() as conn:
        conn.execute(
            update(runs)
            .where(runs.c.id == first["run_id"])
            .values(status="succeeded", finished_at=time.time() - 31 * 86400)
        )
        conn.execute(
            update(runs)
            .where(runs.c.id == second["run_id"])
            .values(status="interrupted", finished_at=time.time() - 31 * 86400)
        )
    assert expire_runtime(db) == [first["run_id"]]
    assert not stores[0].root.exists() and stores[1].root.exists()


async def test_last_assistant_tool_cycle_can_be_compacted(setup_chat):
    from agentscope.message import ToolCallBlock, ToolResultBlock

    from agentbuddy.providers.native import encode_messages

    _, db = setup_chat
    tools = SimpleNamespace(
        store=RuntimeStore(db, "p", "r"), control=SimpleNamespace(event=lambda *a: None)
    )
    runtime = ContextRuntime(tools, {"context_policy": {"reserve_tokens": 256}})

    class Model:
        context_size = 10000
        parameters = SimpleNamespace(max_tokens=512)

        def encode(self, messages, tools=None, choice=None):
            return "", {}, {"messages": encode_messages(messages), "tools": tools}

        async def __call__(self, messages):
            async def stream():
                yield SimpleNamespace(
                    content=[TextBlock(text="File inspected; pending final response.")]
                )

            return stream()

    messages = [
        Msg(name="user", role="user", content=[TextBlock(text="Keep the budget at 100")]),
        Msg(
            name="agent",
            role="assistant",
            content=[
                ToolCallBlock(id="call", name="read_file", input="{}"),
                ToolResultBlock(id="call", name="read_file", output="data " * 2700),
            ],
        ),
    ]
    prepared = await runtime.prepare(Model(), messages, [], None)
    assert any(m.content[0].text == "Keep the budget at 100" for m in prepared)
    assert runtime.store.get("context")["consumed"] == 2
    assert runtime.cost(Model(), prepared, [], None) < 6000

    record = runtime.store.get("context")
    messages[-1].content.extend(
        [
            ToolCallBlock(id="next", name="read_file", input="{}"),
            ToolResultBlock(id="next", name="read_file", output="small new data"),
        ]
    )
    again = await runtime.prepare(Model(), messages, [], None)
    assert runtime.store.get("context") == record
    assert any("small new data" in str(m.content) for m in again)


async def test_auto_team_can_finish_simple_task_without_forced_delegation(setup_chat):
    from agentbuddy.catalog.capabilities import Capabilities
    from agentbuddy.persistence.repository import QueueRepository
    from agentbuddy.runtime.executor import Executor
    from agentbuddy.runtime.repository import RunRepository

    _, db = setup_chat
    _, _, factory = await test_chat.configure(db)
    Capabilities(db).seed_presets()
    repo = RunRepository(db)
    accepted = repo.submit({"message": "hello", "selection": {"mode": "team"}}, "simple-team")
    claimed = repo.claim(QueueRepository(db).start_worker())
    assert claimed["snapshot_json"]["candidates"]
    await Executor(db, factory).execute(claimed)
    assert repo.get(accepted["run_id"])["status"] == "succeeded"
