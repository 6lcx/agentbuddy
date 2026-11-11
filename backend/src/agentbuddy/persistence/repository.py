import os
import time
from uuid import uuid4

from sqlalchemy import insert, select, update

from agentbuddy.persistence.schema import events, operations, workers


def stamp(now=None):
    return time.time() if now is None else now


class QueueRepository:
    def __init__(self, database):
        self.db = database

    def _event(self, conn, operation_id, kind, payload, now):
        conn.execute(
            insert(events).values(
                operation_id=operation_id, type=kind, payload_json=payload, created_at=now
            )
        )

    def enqueue(self, kind, payload):
        now = stamp()
        row = dict(
            id=str(uuid4()), kind=kind, payload_json=payload, status="queued", created_at=now
        )
        with self.db.write() as conn:
            conn.execute(insert(operations).values(**row))
            self._event(conn, row["id"], "operation.queued", {}, now)
            # Return the creation snapshot before a Worker can advance the state.
            return dict(
                conn.execute(select(operations).where(operations.c.id == row["id"]))
                .mappings()
                .one()
            )

    def get(self, operation_id):
        with self.db.engine.connect() as conn:
            row = (
                conn.execute(select(operations).where(operations.c.id == operation_id))
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def events(self, operation_id, after=0, limit=100):
        with self.db.engine.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    select(events)
                    .where(events.c.operation_id == operation_id, events.c.id > after)
                    .order_by(events.c.id)
                    .limit(limit)
                ).mappings()
            ]

    def start_worker(self, now=None):
        worker_id = str(uuid4())
        with self.db.write() as conn:
            conn.execute(
                insert(workers).values(
                    id=worker_id,
                    boot_id=str(uuid4()),
                    pid=os.getpid(),
                    started_at=stamp(now),
                    heartbeat_at=stamp(now),
                    status="running",
                )
            )
        return worker_id

    def heartbeat(self, worker_id, now=None):
        with self.db.write() as conn:
            return (
                conn.execute(
                    update(workers)
                    .where(workers.c.id == worker_id, workers.c.status == "running")
                    .values(heartbeat_at=stamp(now))
                ).rowcount
                == 1
            )

    def claim(self, worker_id):
        with self.db.write() as conn:
            if conn.scalar(select(workers.c.status).where(workers.c.id == worker_id)) != "running":
                return None
            row = (
                conn.execute(
                    select(operations)
                    .where(operations.c.status == "queued")
                    .order_by(operations.c.created_at, operations.c.id)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            claimed = dict(row)
            claimed.update(
                status="running", worker_id=worker_id, claim_token=str(uuid4()), claimed_at=stamp()
            )
            conn.execute(
                update(operations)
                .where(operations.c.id == row["id"], operations.c.status == "queued")
                .values(**claimed)
            )
            self._event(conn, row["id"], "operation.running", {}, claimed["claimed_at"])
            return claimed

    def finish(self, claimed, *, result=None, error=None):
        now = stamp()
        status = "failed" if error is not None else "succeeded"
        with self.db.write() as conn:
            changed = conn.execute(
                update(operations)
                .where(
                    operations.c.id == claimed["id"],
                    operations.c.status == "running",
                    operations.c.worker_id == claimed["worker_id"],
                    operations.c.claim_token == claimed["claim_token"],
                )
                .values(status=status, result_json=result, error_json=error, finished_at=now)
            ).rowcount
            if changed:
                self._event(conn, claimed["id"], f"operation.{status}", {"error": error}, now)
            return changed == 1

    def _interrupt(self, conn, worker_id, now):
        ids = conn.scalars(
            select(operations.c.id).where(
                operations.c.worker_id == worker_id, operations.c.status == "running"
            )
        ).all()
        error = {
            "code": "worker_interrupted",
            "message": "Worker stopped before completion; not replayed",
        }
        for operation_id in ids:
            conn.execute(
                update(operations)
                .where(operations.c.id == operation_id)
                .values(status="failed", error_json=error, finished_at=now)
            )
            self._event(conn, operation_id, "operation.failed", {"error": error}, now)

    def recover_previous_workers(self, now=None):
        """Only call while holding the exclusive Worker instance lock."""
        now = stamp(now)
        with self.db.write() as conn:
            for worker_id in conn.scalars(
                select(workers.c.id).where(workers.c.status == "running")
            ).all():
                self._interrupt(conn, worker_id, now)
                conn.execute(update(workers).where(workers.c.id == worker_id).values(status="lost"))

    def stop_worker(self, worker_id):
        now = stamp()
        with self.db.write() as conn:
            self._interrupt(conn, worker_id, now)
            conn.execute(
                update(workers)
                .where(workers.c.id == worker_id)
                .values(status="stopped", heartbeat_at=now)
            )

    def worker_status(self, stale_seconds=30, now=None):
        with self.db.engine.connect() as conn:
            row = (
                conn.execute(select(workers).order_by(workers.c.started_at.desc()).limit(1))
                .mappings()
                .first()
            )
        if row is None:
            return {"status": "not_started", "heartbeat_at": None, "id": None}
        result = dict(row)
        if result["status"] == "running" and stamp(now) - result["heartbeat_at"] > stale_seconds:
            result["status"] = "stale"
        return result
