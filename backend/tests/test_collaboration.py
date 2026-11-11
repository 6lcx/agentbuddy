import asyncio
import time

import pytest
import test_chat
from test_capabilities import running

from agentbuddy.runtime.collaboration import Supervisor, validate_plan
from agentbuddy.runtime.control import Control
from agentbuddy.runtime.files import Files


def test_plan_rejects_cycles_and_unknown_experts():
    with pytest.raises(ValueError):
        validate_plan(
            [
                {"id": "a", "expert_id": "x", "instruction": "a", "depends_on": ["b"]},
                {"id": "b", "expert_id": "x", "instruction": "b", "depends_on": ["a"]},
            ],
            {"x"},
        )
    with pytest.raises(ValueError):
        validate_plan([{"id": "a", "expert_id": "unknown", "instruction": "a"}], {"x"})
    assert (
        len(
            validate_plan(
                [
                    {"id": "a", "expert_id": "x", "instruction": "a"},
                    {"id": "b", "expert_id": "x", "instruction": "b", "depends_on": ["a"]},
                ],
                {"x"},
            )
        )
        == 2
    )


@pytest.fixture
def setup_chat(tmp_path):
    yield from test_chat.setup_chat.__wrapped__(tmp_path)


async def test_parallel_dependencies_isolation_and_conflicts(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db)
    claimed["snapshot_json"]["candidates"] = [
        {"capabilities": {"expert": {"id": "a"}, "skills": []}}
    ]
    root = Files(db).workspace(claimed)
    Control(db, claimed["id"]).lane("active")
    intervals = {}

    async def child(snapshot, instruction, directory, lane):
        name = instruction.split("\n")[0]
        assert snapshot["capabilities"]["skills"] == []
        intervals[name] = [time.monotonic()]
        if name == "dependent":
            assert (directory / "left.txt").read_text() == "left"
            assert (directory / "right.txt").read_text() == "right"
        await asyncio.sleep(0.15)
        (directory / (name + ".txt")).write_text(name)
        if name in ("left", "right"):
            (directory / "conflict.txt").write_text(name)
        intervals[name].append(time.monotonic())
        return name

    supervisor = Supervisor(db, claimed, root, child)
    result = await supervisor.delegate(
        [
            {"id": "l", "expert_id": "a", "instruction": "left"},
            {"id": "r", "expert_id": "a", "instruction": "right"},
            {"id": "d", "expert_id": "a", "instruction": "dependent", "depends_on": ["l"]},
        ]
    )
    # A single dependency does not inherit a sibling's files.
    assert result["tasks"]["d"]["status"] == "failed"
    assert intervals["left"][0] < intervals["right"][1]
    assert intervals["right"][0] < intervals["left"][1]
    assert not (root / "conflict.txt").exists()
    assert result["conflicts"]["conflict.txt"]
    supervisor.resolve("conflict.txt", result["conflicts"]["conflict.txt"][0])
    assert (root / "conflict.txt").read_text() in ("left", "right")
    assert not supervisor.conflicts


async def test_wait_only_pauses_when_all_lanes_settle(setup_chat):
    _, db = setup_chat
    repo, claimed, _ = await running(db)
    main = Control(db, claimed["id"])
    one = Control(db, claimed["id"], lane_id="one")
    two = Control(db, claimed["id"], lane_id="two")
    main.lane("active")
    one.lane("active")
    two.lane("active")
    main.lane("suspended")
    one.lane("waiting")
    assert main.state()["active_since"] is not None
    two.lane("done")
    assert main.state()["active_since"] is None
    await one.resume()
    assert main.state()["active_since"] is not None
    main.cancel()
    assert repo.get(claimed["id"])["status"] == "failed"


async def test_dependency_completion_and_three_child_limit(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db)
    claimed["snapshot_json"]["candidates"] = [
        {"capabilities": {"expert": {"id": "a"}, "skills": []}}
    ]
    root = Files(db).workspace(claimed)
    Control(db, claimed["id"]).lane("active")
    count = 0
    maximum = 0
    done = set()

    async def child(snapshot, instruction, directory, lane):
        nonlocal count, maximum
        name = instruction.split("\n")[0]
        count += 1
        maximum = max(count, maximum)
        try:
            if name == "final":
                assert {"0", "1"} <= done
                assert (directory / "0.txt").read_text() == "0"
                assert (directory / "1.txt").read_text() == "1"
            await asyncio.sleep(0.1)
            (directory / (name + ".txt")).write_text(name)
            done.add(name)
            return name
        finally:
            count -= 1

    supervisor = Supervisor(db, claimed, root, child)
    result = await supervisor.delegate(
        [{"id": str(i), "expert_id": "a", "instruction": str(i)} for i in range(5)]
        + [{"id": "final", "expert_id": "a", "instruction": "final", "depends_on": ["0", "1"]}]
    )
    assert 2 <= maximum <= 3
    assert all(t["status"] == "succeeded" for t in result["tasks"].values())
    assert (root / "final.txt").read_text() == "final"


async def test_team_snapshot_does_not_inherit_overrides_and_presets_idempotent(setup_chat):
    from test_chat import configure

    from agentbuddy.catalog.capabilities import Capabilities
    from agentbuddy.runtime.repository import RunRepository
    from agentbuddy.runtime.tools import RunTools

    _, db = setup_chat
    await configure(db)
    catalog = Capabilities(db)
    catalog.seed_presets()
    catalog.seed_presets()
    assert len(catalog.list("expert")) == 5
    assert len(catalog.list("skill")) == 5
    excel = next(
        s for s in catalog.list("skill") if s["config_json"].get("preset_key") == "office-excel"
    )
    developer = next(
        e for e in catalog.list("expert") if e["config_json"].get("preset_key") == "developer"
    )
    repo = RunRepository(db)
    submitted = repo.submit(
        {"message": "team", "selection": {"mode": "team", "add_skills": [excel["id"]]}}, "snapshot"
    )
    frozen = repo.get(submitted["run_id"])["snapshot_json"]
    child = next(
        c for c in frozen["candidates"] if c["capabilities"]["expert"]["id"] == developer["id"]
    )
    assert excel["id"] not in [s["id"] for s in child["capabilities"]["skills"]]
    plain = repo.submit({"message": "ordinary"}, "ordinary")
    claimed = repo.get(plain["run_id"])
    assert claimed["snapshot_json"]["candidates"] == []
    tools = RunTools(db, claimed, Control(db, plain["run_id"]), Files(db).workspace(claimed))
    assert tools.supervisor is None


async def test_development_preset_runs_real_project_test(setup_chat):
    import json

    from agentbuddy.catalog.capabilities import Capabilities
    from agentbuddy.runtime.tools import RunTools

    _, db = setup_chat
    catalog = Capabilities(db)
    catalog.seed_presets()
    expert = next(
        e for e in catalog.list("expert") if e["config_json"].get("preset_key") == "developer"
    )
    _, claimed, _ = await running(db, {"selection": {"expert_id": expert["id"]}})
    root = Files(db).workspace(claimed)
    (root / "calculate.py").write_text("def add(a,b): return a+b\n", encoding="utf-8")
    (root / "test_calculate.py").write_text(
        'from calculate import add\nassert add(12,8)==20\nprint("verified 20")\n', encoding="utf-8"
    )
    tools = RunTools(db, claimed, Control(db, claimed["id"]), root)
    skill_id = next(k for k, v in tools.skills.items() if v.get("preset_key") == "development")
    await tools.load_skill(skill_id)
    result = await tools.run_script(skill_id, "scripts/run_python.py", ["test_calculate.py"])
    data = json.loads(result.content[0].text)
    assert data["exit_code"] == 0 and "verified 20" in data["output"]


async def test_waiting_run_releases_global_slot_and_resume_checks_capacity(setup_chat):
    import os

    from test_chat import configure

    from agentbuddy.persistence.repository import QueueRepository
    from agentbuddy.runtime.repository import RunRepository

    _, db = setup_chat
    await configure(db)
    repo = RunRepository(db)
    for index in range(3):
        repo.submit({"message": str(index)}, os.urandom(8).hex())
    worker = QueueRepository(db).start_worker()
    first, second = repo.claim(worker), repo.claim(worker)
    assert first and second and repo.claim(worker) is None
    paused = Control(db, first["id"])
    paused.lane("waiting")
    third = repo.claim(worker)
    assert third
    assert not paused.lane("active", enforce_capacity=True)
    assert repo.finish(second, text="done")
    assert paused.lane("active", enforce_capacity=True)


async def test_two_runs_six_children_shared_sqlite_without_lost_budget(setup_chat):
    from test_chat import configure

    from agentbuddy.persistence.repository import QueueRepository
    from agentbuddy.runtime.repository import RunRepository

    _, db = setup_chat
    await configure(db)
    repo = RunRepository(db)
    for key in ("one", "two"):
        repo.submit({"message": key, "calls_limit": 100}, key)
    worker = QueueRepository(db).start_worker()
    claimed = [repo.claim(worker), repo.claim(worker)]
    concurrent = 0
    maximum = 0

    async def child(snapshot, instruction, root, lane):
        nonlocal concurrent, maximum
        concurrent += 1
        maximum = max(maximum, concurrent)
        try:
            for index in range(15):
                await asyncio.to_thread(lane.check, reserve=True)
                await asyncio.sleep(0.01)
            (root / (instruction.split("\n")[0] + ".txt")).write_text("done")
            return "done"
        finally:
            concurrent -= 1

    supervisors = []
    for run in claimed:
        run["snapshot_json"]["candidates"] = [{"capabilities": {"expert": {"id": "a"}}}]
        Control(db, run["id"]).lane("active")
        supervisors.append(Supervisor(db, run, Files(db).workspace(run), child))
    result = await asyncio.gather(
        *(
            s.delegate([{"id": str(i), "expert_id": "a", "instruction": str(i)} for i in range(3)])
            for s in supervisors
        )
    )
    assert maximum == 6
    assert all(t["status"] == "succeeded" for r in result for t in r["tasks"].values())
    assert all(Control(db, r["id"]).state()["calls_used"] == 46 for r in claimed)


async def test_delete_conflict_dependency_and_empty_version(setup_chat):
    _, db = setup_chat
    repo, claimed, _ = await running(db)
    claimed["snapshot_json"]["candidates"] = [{"capabilities": {"expert": {"id": "a"}}}]
    root = Files(db).workspace(claimed)
    (root / "old.txt").write_text("old")
    version = Files(db).stage_version(claimed, root)
    assert version is not None
    Control(db, claimed["id"]).lane("active")

    async def child(snapshot, instruction, directory, lane):
        action = instruction.split("\n")[0]
        if action == "delete":
            (directory / "old.txt").unlink()
        elif action == "modify":
            (directory / "old.txt").write_text("modified")
        elif action == "dependent":
            assert not (directory / "old.txt").exists()
            (directory / "new.txt").write_text("renamed")
        return action

    supervisor = Supervisor(db, claimed, root, child)
    result = await supervisor.delegate(
        [
            {"id": "delete", "expert_id": "a", "instruction": "delete"},
            {"id": "modify", "expert_id": "a", "instruction": "modify"},
            {
                "id": "dependent",
                "expert_id": "a",
                "instruction": "dependent",
                "depends_on": ["delete"],
            },
        ]
    )
    assert result["tasks"]["dependent"]["status"] == "succeeded"
    assert (root / "old.txt").read_text() == "old"
    assert "old.txt" in result["conflicts"]
    supervisor.resolve("old.txt", result["tasks"]["dependent"]["task_id"])
    assert not (root / "old.txt").exists()
    assert (root / "new.txt").read_text() == "renamed"


async def test_dependent_edit_supersedes_ancestor(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db)
    claimed["snapshot_json"]["candidates"] = [{"capabilities": {"expert": {"id": "a"}}}]
    root = Files(db).workspace(claimed)
    Control(db, claimed["id"]).lane("active")

    async def child(snapshot, instruction, directory, lane):
        action = instruction.split("\n")[0]
        (directory / "result.txt").write_text(action)
        return action

    supervisor = Supervisor(db, claimed, root, child)
    result = await supervisor.delegate(
        [
            {"id": "first", "expert_id": "a", "instruction": "first"},
            {"id": "second", "expert_id": "a", "instruction": "second", "depends_on": ["first"]},
        ]
    )
    assert not result["conflicts"]
    assert (root / "result.txt").read_text() == "second"
