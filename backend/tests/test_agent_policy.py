import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
import test_chat
from pydantic import ValidationError
from test_capabilities import running

from agentbuddy.providers.native import ProviderFailure
from agentbuddy.runtime.agent_policy import (
    AgentPolicy,
    LaneControl,
    resolve_agent_policy,
    settle_run_budgets,
)
from agentbuddy.runtime.control import Control


@pytest.fixture
def setup_chat(tmp_path):
    yield from test_chat.setup_chat.__wrapped__(tmp_path)


def test_defaults_overrides_and_hard_ceiling():
    assert resolve_agent_policy({}).max_iters == 50
    assert resolve_agent_policy({}, "child").max_iters == 20
    with pytest.raises(ValidationError):
        AgentPolicy(max_iters=101)
    with pytest.raises(ValidationError):
        AgentPolicy(max_iters=True)
    snapshot = {"capabilities": {"expert": {"config_json": {"agent_policy": {"max_iters": 15}}}}}
    assert resolve_agent_policy(snapshot, "child", {"max_iters": 10}).max_iters == 10
    with pytest.raises(ValueError, match="不得扩大"):
        resolve_agent_policy(snapshot, "child", {"max_iters": 16})
    with pytest.raises(ValueError, match="派发准则"):
        resolve_agent_policy(snapshot, "child", {"require_parallel": False})
    snapshot["agent_policy"] = {"max_iters": 10}
    assert resolve_agent_policy(snapshot, "child").max_iters == 10


async def test_local_quota_atomic_and_persists_across_wrapper_recreation(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db, {"calls_limit": 20})
    policy = AgentPolicy(calls_limit=3)
    lane = LaneControl(Control(db, claimed["id"], lane_id="child"), policy)
    lane.lane("active")

    def reserve(_):
        other = LaneControl(Control(db, claimed["id"], lane_id="child"), policy)
        try:
            other.check(reserve=True)
            return True
        except ProviderFailure as exc:
            assert exc.code == "agent_budget_exhausted"
            return False

    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(reserve, range(12))) == 3
    assert lane.budget_state()["calls_used"] == 3
    assert lane.state()["calls_used"] == 3
    restored = LaneControl(Control(db, claimed["id"], lane_id="child"), policy)
    with pytest.raises(ProviderFailure, match="调用次数"):
        restored.check(reserve=True)
    with pytest.raises(ProviderFailure, match="配置不一致"):
        LaneControl(Control(db, claimed["id"], lane_id="child"), AgentPolicy(calls_limit=4))


async def test_shared_quota_cannot_be_reset_by_new_lane(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db, {"calls_limit": 2})
    for name in ("first", "replacement"):
        LaneControl(Control(db, claimed["id"], lane_id=name), AgentPolicy()).check(reserve=True)
    with pytest.raises(ProviderFailure) as exc:
        LaneControl(Control(db, claimed["id"], lane_id="third"), AgentPolicy()).check(reserve=True)
    assert exc.value.code == "budget_exhausted"
    assert Control(db, claimed["id"]).state()["calls_used"] == 2


async def test_shared_cancellation_still_stops_lane(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db)
    lane = LaneControl(Control(db, claimed["id"]), AgentPolicy())
    lane.shared.cancel()
    with pytest.raises(ProviderFailure) as exc:
        lane.check()
    assert exc.value.code == "run_stopped"


async def test_iterations_separate_from_calls_and_waiting_pauses_elapsed(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db)
    now = [100.0]
    shared = Control(db, claimed["id"], clock=lambda: now[0], lane_id="timed")
    lane = LaneControl(shared, AgentPolicy(max_iters=2, seconds_limit=5))
    lane.lane("active")
    lane.reserve_iteration()
    lane.check(reserve=True)
    lane.check(reserve=True)  # Retry/tool charges a call, not a decision.
    assert lane.budget_state()["iterations_used"] == 1
    lane.reserve_iteration()
    with pytest.raises(ProviderFailure) as exc:
        lane.reserve_iteration()
    assert exc.value.code == "agent_iterations_exhausted"
    now[0] += 2
    lane.lane("waiting")
    now[0] += 100
    assert lane.check() == 3
    await lane.resume()
    now[0] += 3
    with pytest.raises(ProviderFailure) as exc:
        lane.check()
    assert exc.value.code == "agent_budget_exhausted"


async def test_guard_and_confirmation_do_not_bypass_lane_limits(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db)
    lane = LaneControl(Control(db, claimed["id"]), AgentPolicy(calls_limit=1))
    lane.lane("active")
    assert await lane.guard(asyncio.sleep(0, result="ok")) == "ok"
    with pytest.raises(ProviderFailure, match="调用次数"):
        await lane.guard(asyncio.sleep(0))
    assert lane.state()["calls_used"] == 1
    pending = asyncio.create_task(lane.confirm({"kind": "external_write", "text": "exact"}))
    for _ in range(30):
        if lane.pending():
            break
        await asyncio.sleep(0.01)
    assert lane.budget_state()["active_since"] is None
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert lane.budget_state()["active_since"] is not None


async def test_terminal_done_still_settles_local_budget(setup_chat):
    _, db = setup_chat
    repo, claimed, _ = await running(db)
    now = [100.0]
    lane = LaneControl(Control(db, claimed["id"], clock=lambda: now[0]), AgentPolicy())
    lane.lane("active")
    lane.check(reserve=True)
    lane.reserve_iteration()
    now[0] = 107
    repo.finish(claimed, text="done")
    assert lane.lane("done") is False
    assert lane.budget_state()["active_since"] is None
    assert lane.budget_state()["elapsed"] == 7
    now[0] = 1000
    lane.stop()
    assert lane.budget_state()["elapsed"] == 7
    assert lane.budget_state()["calls_used"] == 1
    assert lane.budget_state()["iterations_used"] == 1


async def test_reconcile_inside_db_write_preserves_counts_and_excludes_known_downtime(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db)
    now = [100.0]
    control = Control(db, claimed["id"], clock=lambda: now[0])
    lane = LaneControl(control, AgentPolicy())
    lane.lane("active")
    lane.check(reserve=True)
    lane.reserve_iteration()
    now[0] = 1000  # Restart much later; actual stop was at 110.
    with db.write():
        result = settle_run_budgets(db.path.parent, claimed["id"], stopped_at=110)
    assert result["main"]["elapsed"] == 10
    restored = LaneControl(control, AgentPolicy())
    restored.reconcile_interruption(stopped_at=110)  # Idempotent.
    assert restored.budget_state()["calls_used"] == 1
    assert restored.budget_state()["iterations_used"] == 1
    await restored.resume()
    now[0] = 1002
    restored.stop()
    assert restored.budget_state()["elapsed"] == 12


async def test_shared_elapsed_fallback_conservatively_settles_active_lane_only(setup_chat):
    _, db = setup_chat
    _, claimed, _ = await running(db)
    now = [100.0]
    lane = LaneControl(Control(db, claimed["id"], clock=lambda: now[0]), AgentPolicy())
    lane.lane("active")
    now[0] = 900
    with pytest.raises(ValueError, match="可信停止时间"):
        lane.reconcile_interruption()
    lane.reconcile_interruption(shared_elapsed=15)
    assert lane.budget_state()["elapsed"] == 15
    lane.reconcile_interruption(shared_elapsed=99)
    assert lane.budget_state()["elapsed"] == 15  # Done/waiting lane isn't charged again.
    await lane.resume()
    with pytest.raises(ValueError, match="过期结算"):
        lane.reconcile_interruption(stopped_at=110)
    assert lane.budget_state()["active_since"] == 900


async def test_settlement_lock_contention_fails_fast_without_db_deadlock(setup_chat):
    from filelock import FileLock, Timeout

    _, db = setup_chat
    _, claimed, _ = await running(db)
    lane = LaneControl(Control(db, claimed["id"]), AgentPolicy())
    with FileLock(str(lane.path) + ".lock"):
        with db.write():
            with pytest.raises(Timeout):
                lane.reconcile_interruption(shared_elapsed=1)
