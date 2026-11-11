"""Frozen per-agent limits layered over the existing shared run budget."""

import json
import math
import os
from pathlib import Path
from typing import Literal

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from agentbuddy.providers.native import ProviderFailure
from agentbuddy.runtime.control import Control


def _settle_row(row, *, stopped_at=None, shared_elapsed=None):
    if stopped_at is None and shared_elapsed is None:
        raise ValueError("结算需要可信停止时间或共享已消耗时长")
    for value in (stopped_at, shared_elapsed):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("预算结算时间必须为有限非负数")
    since = row["active_since"]
    if since is not None:
        if stopped_at is not None:
            if stopped_at < since:
                raise ValueError("停止时间早于当前活动区间，拒绝过期结算")
            row["elapsed"] += stopped_at - since
        if shared_elapsed is not None:
            # Global active time is a conservative upper bound for this lane.
            row["elapsed"] = max(row["elapsed"], shared_elapsed)
        row["active_since"] = None
        row["settled_at"] = stopped_at
    return row


def settle_lane_budget(path, *, stopped_at=None, shared_elapsed=None):
    """Pure-file, idempotent settlement; safe inside a DB transaction.

    Never wait for the file lock while the caller might hold the DB write lock:
    a live lane may hold its file lock while checking shared SQLite quotas.
    FileLock Timeout must be retried AFTER releasing the database transaction.
    The caller must stop/join execution before reconciling an interrupted run.
    """
    path = Path(path)
    with FileLock(str(path) + ".lock", timeout=0):
        if not path.exists():
            return None
        row = _settle_row(
            json.loads(path.read_text(encoding="utf-8")),
            stopped_at=stopped_at,
            shared_elapsed=shared_elapsed,
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(row), encoding="utf-8")
        os.replace(temporary, path)
        return row


def settle_run_budgets(data_dir, run_id, *, stopped_at=None, shared_elapsed=None):
    """Settle existing lane ledgers only; no database reads or writes."""
    if not run_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id
    ):
        raise ValueError("无效的任务标识")
    folder = Path(data_dir) / "agent_budgets" / run_id
    return {
        path.stem: settle_lane_budget(path, stopped_at=stopped_at, shared_elapsed=shared_elapsed)
        for path in sorted(folder.glob("*.json"))
    }


class AgentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    max_iters: int = Field(default=50, ge=1, le=100)
    calls_limit: int = Field(default=120, ge=1, le=500)
    seconds_limit: int = Field(default=1800, ge=1, le=7200)
    min_delegate_iterations: int = Field(default=4, ge=1, le=100)
    require_parallel: bool = True
    require_isolated: bool = True
    allow_serial_dependency_exception: bool = False
    allow_user_requested_exception: bool = False


def resolve_agent_policy(snapshot, role: Literal["main", "child"] = "main", override=None):
    """Task overrides can narrow but never expand a frozen candidate's limits."""
    defaults = (
        AgentPolicy(max_iters=20, calls_limit=60, seconds_limit=900)
        if role == "child"
        else AgentPolicy()
    )
    values = defaults.model_dump()
    expert = snapshot.get("capabilities", {}).get("expert") or {}
    values.update(expert.get("config_json", {}).get("agent_policy") or {})
    values.update(snapshot.get("agent_policy") or {})
    policy = AgentPolicy.model_validate(values)
    if override is not None:
        narrowed = AgentPolicy.model_validate({**policy.model_dump(), **override})
        for key in ("max_iters", "calls_limit", "seconds_limit"):
            if getattr(narrowed, key) > getattr(policy, key):
                raise ValueError(f"子任务不得扩大专家预算：{key}")
        for key in AgentPolicy.model_fields.keys() - {"max_iters", "calls_limit", "seconds_limit"}:
            if getattr(narrowed, key) != getattr(policy, key):
                raise ValueError("子任务不能修改派发准则")
        policy = narrowed
    return policy


class LaneControl:
    """Duck-types Control; persistent lane quotas never reset shared consumption.

    Call reserve_iteration once per model decision (not per transport retry).
    Model attempts, summaries and tools use check(reserve=True) / guard.
    Existing Control methods are bound to this wrapper so their internal checks
    cannot bypass the lane quota. A crash between local and shared reservation
    conservatively consumes a local slot, never refunds shared work.
    """

    def __init__(self, control, policy: AgentPolicy | dict, *, storage_path=None):
        self.shared = control
        self.policy = (
            policy if isinstance(policy, AgentPolicy) else AgentPolicy.model_validate(policy)
        )
        # Identifiers come from server-generated run/lane IDs; reject path injection.
        for identifier in (control.run_id, control.lane_id):
            if not identifier or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for c in identifier
            ):
                raise ValueError("无效的 Agent 预算标识")
        self.path = (
            Path(storage_path)
            if storage_path
            else control.db.path.parent
            / "agent_budgets"
            / control.run_id
            / f"{control.lane_id}.json"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.path) + ".lock")
        with self.lock:
            if self.path.exists():
                self._load()  # Validate frozen policy and counters before reuse.
            else:
                self._save(
                    {
                        "policy": self.policy.model_dump(),
                        "calls_used": 0,
                        "iterations_used": 0,
                        "elapsed": 0.0,
                        "active_since": None,
                    }
                )

    def __getattr__(self, name):
        return getattr(self.shared, name)

    def _load(self):
        row = json.loads(self.path.read_text(encoding="utf-8"))
        if row["policy"] != self.policy.model_dump():
            raise ProviderFailure("agent_policy_changed", "恢复时 Agent 预算配置不一致")
        if any(row[k] < 0 for k in ("calls_used", "iterations_used", "elapsed")):
            raise ProviderFailure("agent_budget_invalid", "Agent 预算记录损坏")
        return row

    def _save(self, row):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(row), encoding="utf-8")
        os.replace(temporary, self.path)

    def budget_state(self):
        with self.lock:
            return self._load()

    def stop(self, *, stopped_at=None, shared_elapsed=None):
        """Settle locally even if the shared run is terminal; no DB transaction."""
        if stopped_at is None and shared_elapsed is None:
            stopped_at = self.clock()
        return settle_lane_budget(self.path, stopped_at=stopped_at, shared_elapsed=shared_elapsed)

    def reconcile_interruption(self, *, stopped_at=None, shared_elapsed=None):
        """Stop old accounting before resume; requires trusted recovery evidence."""
        return settle_lane_budget(self.path, stopped_at=stopped_at, shared_elapsed=shared_elapsed)

    def _remaining(self, row):
        elapsed = row["elapsed"] + (
            max(0, self.clock() - row["active_since"]) if row["active_since"] is not None else 0
        )
        remaining = self.policy.seconds_limit - elapsed
        if remaining <= 0:
            raise ProviderFailure("agent_budget_exhausted", "当前 Agent 执行时长预算已用尽")
        return remaining

    def check(self, reserve=False):
        with self.lock:
            row = self._load()
            remaining = self._remaining(row)
            if reserve:
                if row["calls_used"] >= self.policy.calls_limit:
                    raise ProviderFailure("agent_budget_exhausted", "当前 Agent 调用次数预算已用尽")
                row["calls_used"] += 1
                self._save(row)
            return min(remaining, self.shared.check(reserve=reserve))

    def reserve_iteration(self):
        with self.lock:
            row = self._load()
            self._remaining(row)
            self.shared.check()
            if row["iterations_used"] >= self.policy.max_iters:
                raise ProviderFailure("agent_iterations_exhausted", "当前 Agent 模型决策轮数已用尽")
            row["iterations_used"] += 1
            self._save(row)
            return self.policy.max_iters - row["iterations_used"]

    def lane(self, state, enforce_capacity=False):
        with self.lock:
            row = self._load()
            accepted = self.shared.lane(state, enforce_capacity=enforce_capacity)
            if not accepted:
                if state != "active":
                    self._save(_settle_row(row, stopped_at=self.clock()))
                return False
            now = self.clock()
            if row["active_since"] is not None:
                row["elapsed"] += max(0, now - row["active_since"])
            row["active_since"] = now if state == "active" else None
            self._save(row)
            return True

    async def resume(self):
        self.check()
        return await Control.resume(self)

    async def guard(self, awaitable, *, timeout=120):
        return await Control.guard(self, awaitable, timeout=timeout)

    async def confirm(self, payload):
        return await Control.confirm(self, payload)
