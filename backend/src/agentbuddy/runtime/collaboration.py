"""One-level AgentScope delegation with isolated workspaces and explicit merge conflicts."""

import asyncio
import json
import shutil
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, update

from agentbuddy.persistence.schema import subtasks
from agentbuddy.providers.native import ProviderFailure
from agentbuddy.runtime.control import Control
from agentbuddy.runtime.files import manifest, relative


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    expert_id: str
    instruction: str = Field(min_length=1, max_length=16000)
    depends_on: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    replaces: list[str] = Field(default_factory=list)


def validate_plan(tasks, candidates):
    if not 1 <= len(tasks) <= 12:
        raise ValueError("每批必须包含 1–12 项任务")
    plan = [TaskPlan.model_validate(t) for t in tasks]
    ids = {t.id for t in plan}
    if len(ids) != len(plan):
        raise ValueError("子任务标识重复")
    for item in plan:
        if item.expert_id not in candidates or not set(item.depends_on) <= ids:
            raise ValueError("专家或依赖不在当前计划范围内")
        for path in item.outputs:
            relative(path)
    ready = set()
    while len(ready) < len(ids):
        batch = {t.id for t in plan if set(t.depends_on) <= ready} - ready
        if not batch:
            raise ValueError("子任务存在循环依赖")
        ready |= batch
    return plan


class Supervisor:
    def __init__(self, db, claimed, root, child_runner):
        self.db, self.claimed, self.root, self.child_runner = db, claimed, root, child_runner
        self.control = Control(db, claimed["id"])
        self.candidates = {
            c["capabilities"]["expert"]["id"]: c
            for c in claimed["snapshot_json"].get("candidates", [])
        }
        self.conflicts = {}
        self.failed = set()
        self.children = {}
        self.unsafe_failures = set()
        self.dispatched = 0

    def record(self, identifier, status, result=None):
        with self.db.write() as conn:
            changes = {"status": status}
            if status == "running":
                changes["started_at"] = time.time()
            if status in ("succeeded", "failed", "blocked", "cancelled"):
                changes.update(result_json=result, finished_at=time.time())
            conn.execute(update(subtasks).where(subtasks.c.id == identifier).values(**changes))
            self.control.event(
                "task.status", {"task_id": identifier, "status": status, "result": result}, conn
            )

    async def delegate(self, tasks):
        plan = validate_plan(tasks, self.candidates)
        by_id = {item.id: item for item in plan}

        def ancestors(key):
            direct = by_id[key].depends_on
            return set(direct).union(*(ancestors(d) for d in direct))

        for item in plan:
            if not set(item.replaces) <= self.failed or set(item.replaces) & self.unsafe_failures:
                raise ValueError("只能替代已失败且没有外部未知结果的子任务")
        self.control.check(reserve=True)
        self.dispatched += len(plan)
        batch = str(uuid4())
        base = self.root.parent / (self.claimed["id"] + "-tasks") / batch
        base.mkdir(parents=True)
        baseline = {f["path"]: f["sha256"] for f in manifest(self.root)}
        results, jobs, identifiers = {}, {}, {}
        semaphore = asyncio.Semaphore(3)
        for item in plan:
            identifier = str(uuid4())
            identifiers[item.id] = identifier
            with self.db.write() as conn:
                conn.execute(
                    insert(subtasks).values(
                        id=identifier,
                        run_id=self.claimed["id"],
                        task_key=item.id,
                        expert_id=item.expert_id,
                        status="queued",
                        plan_json=item.model_dump(),
                    )
                )

        async def run(item):
            identifier = identifiers[item.id]
            try:
                for dependency in item.depends_on:
                    await jobs[dependency]
                if any(results[d]["status"] != "succeeded" for d in item.depends_on):
                    result = {"status": "blocked", "reason": "依赖子任务未完成"}
                    results[item.id] = result
                    self.record(identifier, "blocked", result)
                    self.failed.add(identifier)
                    return
                async with semaphore:
                    # A pending interaction stops new dispatch; already active siblings settle.
                    while any(i["status"] == "pending" for i in self.control.pending()):
                        self.control.check()
                        await asyncio.sleep(0.2)
                    lane = Control(self.db, self.claimed["id"], lane_id=identifier)
                    await lane.resume()
                    child_root = base / identifier
                    try:
                        await asyncio.to_thread(shutil.copytree, self.root, child_root)
                        dependency_hashes = {}
                        effective_dependencies = [
                            d
                            for d in item.depends_on
                            if not any(d in ancestors(other) for other in item.depends_on)
                        ]
                        for dependency in effective_dependencies:
                            dep = results[dependency]
                            for file in dep["files"] + dep.get("deleted", []):
                                source = self.children[dep["task_id"]] / file
                                content = source.read_bytes() if source.is_file() else None
                                if file in dependency_hashes and dependency_hashes[file] != content:
                                    raise ProviderFailure(
                                        "dependency_conflict", "依赖任务输出存在同名冲突"
                                    )
                                dependency_hashes[file] = content
                                target = child_root / file
                                target.parent.mkdir(parents=True, exist_ok=True)
                                if content is None:
                                    target.unlink(missing_ok=True)
                                else:
                                    shutil.copyfile(source, target)
                        self.children[identifier] = child_root
                        self.record(identifier, "running")
                        context = (
                            item.instruction
                            + "\n依赖任务结果："
                            + json.dumps(
                                {d: results[d] for d in item.depends_on}, ensure_ascii=False
                            )
                        )
                        summary = await self.child_runner(
                            self.candidates[item.expert_id], context, child_root, lane
                        )
                        after = {f["path"]: f["sha256"] for f in manifest(child_root)}
                        deleted = sorted(set(baseline) - set(after))
                        files = [
                            f["path"]
                            for f in manifest(child_root)
                            if baseline.get(f["path"]) != f["sha256"]
                        ]
                        if any(not (child_root / p).is_file() for p in item.outputs):
                            raise ProviderFailure("missing_outputs", "子任务缺少计划要求的文件")
                        result = {
                            "task_id": identifier,
                            "status": "succeeded",
                            "summary": summary[:16000],
                            "files": files,
                            "deleted": deleted,
                        }
                        results[item.id] = result
                        self.record(identifier, "succeeded", result)
                        self.failed.difference_update(item.replaces)
                    finally:
                        lane.lane("done")
            except asyncio.CancelledError:
                self.record(identifier, "cancelled")
                raise
            except Exception as exc:
                result = {
                    "task_id": identifier,
                    "status": "failed",
                    "code": getattr(exc, "code", "task_failed"),
                    "reason": str(exc)[:500],
                }
                results[item.id] = result
                self.failed.add(identifier)
                if result["code"] == "external_result_unknown":
                    self.unsafe_failures.add(identifier)
                self.record(identifier, "failed", result)

        # Populate all task handles before their first scheduling turn.
        self.control.lane("suspended")
        try:
            async with asyncio.TaskGroup() as group:
                for item in plan:
                    jobs[item.id] = group.create_task(run(item))
            proposals = {}
            for item in plan:
                result = results[item.id]
                if result["status"] == "succeeded":
                    for path in result["files"] + result.get("deleted", []):
                        proposals.setdefault(path, []).append(result["task_id"])
            # Later dependent edits supersede their ancestors; independent edits conflict.
            prior = {identifiers[t.id]: {identifiers[d] for d in ancestors(t.id)} for t in plan}
            for path, owners in proposals.items():
                owners = [owner for owner in owners if not any(owner in prior[o] for o in owners)]
                contents = {
                    owner: (self.children[owner] / path).read_bytes()
                    if (self.children[owner] / path).is_file()
                    else None
                    for owner in owners
                }
                # Different changes to one path never silently overwrite each other.
                if len(set(contents.values())) > 1:
                    self.conflicts[path] = owners
                    continue
                target = self.root / relative(path)
                current = next(
                    (f["sha256"] for f in manifest(self.root) if f["path"] == path), None
                )
                if current != baseline.get(path):
                    self.conflicts[path] = owners
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                content = next(iter(contents.values()))
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(content)
            return {"tasks": results, "conflicts": self.conflicts}
        finally:
            if self.control.check():
                await self.control.resume()

    def resolve(self, path, task_id):
        if task_id not in self.conflicts.get(path, []):
            raise ValueError("请选择该冲突中的候选产物")
        target = self.root / relative(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self.children[task_id] / relative(path)
        if source.is_file():
            shutil.copyfile(source, target)
        else:
            target.unlink(missing_ok=True)
        del self.conflicts[path]
        self.control.event("task.conflict_resolved", {"path": path, "task_id": task_id})

    def task_list(self):
        with self.db.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(subtasks).where(subtasks.c.run_id == self.claimed["id"])
                ).mappings()
            ]
