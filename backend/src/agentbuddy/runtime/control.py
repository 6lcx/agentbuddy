"""Durable run budget and interaction gate, shared by model and tool calls."""

import asyncio
import hashlib
import json
import time
from uuid import uuid4

from sqlalchemy import func, insert, select, update

from agentbuddy.catalog.service import DomainError
from agentbuddy.persistence.schema import interactions, run_controls, run_lanes, runs
from agentbuddy.providers.native import ProviderFailure


class Control:
    def __init__(self, db, run_id, clock=time.time, lane_id="main"):
        self.db, self.run_id, self.clock = db, run_id, clock
        self.lane_id = lane_id

    def lane(self, state, enforce_capacity=False):
        with self.db.write() as conn:
            control = (
                conn.execute(select(run_controls).where(run_controls.c.run_id == self.run_id))
                .mappings()
                .one()
            )
            if conn.scalar(select(runs.c.status).where(runs.c.id == self.run_id)) != "running":
                return False
            if state == "active" and enforce_capacity and control["active_since"] is None:
                active = conn.scalar(
                    select(func.count())
                    .select_from(run_controls.join(runs))
                    .where(runs.c.status == "running", run_controls.c.active_since.is_not(None))
                )
                if active >= 2:
                    return False
            existing = conn.scalar(
                select(run_lanes.c.id).where(
                    run_lanes.c.run_id == self.run_id, run_lanes.c.id == self.lane_id
                )
            )
            if existing:
                conn.execute(
                    update(run_lanes)
                    .where(run_lanes.c.run_id == self.run_id, run_lanes.c.id == self.lane_id)
                    .values(state=state)
                )
            else:
                conn.execute(
                    insert(run_lanes).values(run_id=self.run_id, id=self.lane_id, state=state)
                )
            active = conn.scalar(
                select(func.count())
                .select_from(run_lanes)
                .where(run_lanes.c.run_id == self.run_id, run_lanes.c.state == "active")
            )
            if not active and control["active_since"] is not None:
                self.settle(conn)
            elif active and control["active_since"] is None:
                conn.execute(
                    update(run_controls)
                    .where(run_controls.c.run_id == self.run_id)
                    .values(active_since=self.clock())
                )
            return True

    async def resume(self):
        while not self.lane("active", enforce_capacity=True):
            self.check()
            await asyncio.sleep(0.2)

    def event(self, kind, payload, conn=None):
        from agentbuddy.runtime.repository import RunRepository

        if conn is not None:
            RunRepository(self.db)._event(conn, self.run_id, kind, payload)
        else:
            with self.db.write() as connection:
                self.event(kind, payload, connection)

    def state(self):
        with self.db.engine.connect() as conn:
            row = (
                conn.execute(select(run_controls).where(run_controls.c.run_id == self.run_id))
                .mappings()
                .first()
            )
            if not row:
                raise DomainError("NOT_FOUND", "任务不存在", 404)
            return dict(row)

    def check(self, reserve=False):
        with self.db.write() as conn:
            row = (
                conn.execute(select(run_controls).where(run_controls.c.run_id == self.run_id))
                .mappings()
                .one()
            )
            status = conn.scalar(select(runs.c.status).where(runs.c.id == self.run_id))
            if status != "running":
                raise ProviderFailure("run_stopped", "任务已停止")
            elapsed = row["elapsed"] + (
                self.clock() - row["active_since"] if row["active_since"] else 0
            )
            if elapsed >= row["seconds_limit"]:
                raise ProviderFailure("budget_exhausted", "执行时长预算已用尽")
            if reserve:
                if row["calls_used"] >= row["calls_limit"]:
                    raise ProviderFailure("budget_exhausted", "调用次数预算已用尽")
                conn.execute(
                    update(run_controls)
                    .where(run_controls.c.run_id == self.run_id)
                    .values(calls_used=row["calls_used"] + 1)
                )
                self.event("budget.reserved", {"calls_used": row["calls_used"] + 1}, conn)
            return row["seconds_limit"] - elapsed

    def settle(self, conn):
        row = (
            conn.execute(select(run_controls).where(run_controls.c.run_id == self.run_id))
            .mappings()
            .first()
        )
        if row:
            elapsed = row["elapsed"] + (
                max(0, self.clock() - row["active_since"]) if row["active_since"] else 0
            )
            conn.execute(
                update(run_controls)
                .where(run_controls.c.run_id == self.run_id)
                .values(elapsed=elapsed, active_since=None)
            )

    def cancel(self):
        self.state()
        from agentbuddy.runtime.repository import RunRepository

        with self.db.write() as conn:
            changed = conn.execute(
                update(runs)
                .where(runs.c.id == self.run_id, runs.c.status.in_(["queued", "running"]))
                .values(
                    status="failed",
                    failure_json={"code": "cancelled", "message": "任务已取消"},
                    finished_at=self.clock(),
                )
            ).rowcount
            if changed:
                from agentbuddy.persistence.schema import subtasks

                conn.execute(
                    update(subtasks)
                    .where(
                        subtasks.c.run_id == self.run_id,
                        subtasks.c.status.in_(["queued", "running"]),
                    )
                    .values(status="cancelled", finished_at=self.clock())
                )
                self.settle(conn)
                conn.execute(
                    update(interactions)
                    .where(interactions.c.run_id == self.run_id, interactions.c.status == "pending")
                    .values(status="cancelled")
                )
                RunRepository(self.db)._event(
                    conn, self.run_id, "run.completed", {"status": "cancelled"}
                )
            return bool(changed)

    def pending(self):
        with self.db.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(interactions)
                    .where(interactions.c.run_id == self.run_id)
                    .order_by(interactions.c.created_at)
                ).mappings()
            ]

    def answer(self, interaction_id, digest, approved, text=None):
        with self.db.write() as conn:
            row = (
                conn.execute(
                    select(interactions).where(
                        interactions.c.id == interaction_id, interactions.c.run_id == self.run_id
                    )
                )
                .mappings()
                .first()
            )
            if not row or row["digest"] != digest:
                raise DomainError("INTERACTION_CONFLICT", "确认内容已变化或不存在")
            if conn.scalar(select(runs.c.status).where(runs.c.id == self.run_id)) != "running":
                raise DomainError("INTERACTION_CLOSED", "任务已经结束")
            if row["status"] != "pending":
                if row["status"] == ("approved" if approved else "rejected"):
                    return
                raise DomainError("INTERACTION_CLOSED", "此确认已结束")
            if self.clock() >= row["expires_at"]:
                raise DomainError("INTERACTION_EXPIRED", "确认已过期")
            conn.execute(
                update(interactions)
                .where(interactions.c.id == interaction_id)
                .values(
                    status="approved" if approved else "rejected",
                    payload_json={
                        **row["payload_json"],
                        **(
                            {"user_answer": text}
                            if text is not None
                            and row["payload_json"].get("kind") == "clarification"
                            else {}
                        ),
                    },
                )
            )
            self.event("interaction.answered", {"id": interaction_id, "approved": approved}, conn)

    async def confirm(self, payload):
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        identifier, now = str(uuid4()), self.clock()
        self.check()
        with self.db.write() as conn:
            row = (
                conn.execute(select(run_controls).where(run_controls.c.run_id == self.run_id))
                .mappings()
                .one()
            )
            conn.execute(
                insert(interactions).values(
                    id=identifier,
                    run_id=self.run_id,
                    digest=digest,
                    payload_json=payload,
                    status="pending",
                    created_at=now,
                    expires_at=now + 86400,
                )
            )
            self.event(
                "interaction.requested",
                {"id": identifier, "digest": digest, "content": payload},
                conn,
            )
        self.lane("waiting")
        try:
            while True:
                self.check()
                row = next(r for r in self.pending() if r["id"] == identifier)
                if row["status"] != "pending":
                    return row["status"] == "approved"
                if self.clock() >= row["expires_at"]:
                    with self.db.write() as conn:
                        conn.execute(
                            update(interactions)
                            .where(interactions.c.id == identifier)
                            .values(status="expired")
                        )
                    raise ProviderFailure("waiting_expired", "等待用户确认已过期")
                await asyncio.sleep(0.2)
        finally:
            with self.db.engine.connect() as conn:
                running = (
                    conn.scalar(select(runs.c.status).where(runs.c.id == self.run_id)) == "running"
                )
            if running:
                await self.resume()

    async def guard(self, awaitable, *, timeout=120):
        try:
            remaining = self.check(reserve=True)
        except BaseException:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise
        task = asyncio.ensure_future(awaitable)
        started = time.monotonic()
        try:
            while not task.done():
                self.check()
                if time.monotonic() - started >= min(timeout, remaining):
                    raise ProviderFailure(
                        "tool_timeout", "调用超时；外部写操作结果可能未知，请核对后再操作"
                    )
                await asyncio.wait([task], timeout=0.2)
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
