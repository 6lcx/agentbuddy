"""Expire terminal runtime references; queued/running/interrupted checkpoints stay pinned."""

import shutil
import time

from sqlalchemy import delete, select

from agentbuddy.persistence.schema import runs, settings_table
from agentbuddy.runtime.context_policy import ContextPolicy


def expire_runtime(db, now=None):
    now = time.time() if now is None else now
    with db.engine.connect() as conn:
        rows = (
            conn.execute(select(runs).where(runs.c.status.in_(["succeeded", "failed"])))
            .mappings()
            .all()
        )
    removed = []
    base = (db.path.parent / "projects").resolve()
    for row in rows:
        days = ContextPolicy.model_validate(
            row["snapshot_json"].get("context_policy", {})
        ).retention_days
        if row["finished_at"] is None or row["finished_at"] + days * 86400 > now:
            continue
        directory = base / row["project_id"] / "runtime" / row["id"]
        if directory.exists():
            if (
                directory.is_symlink()
                or directory.resolve() != directory.absolute()
                or not directory.resolve().is_relative_to(base)
            ):
                raise ValueError("Unsafe runtime cleanup path")
            shutil.rmtree(directory)
        with db.write() as conn:
            conn.execute(
                delete(settings_table).where(settings_table.c.key.like(f"runtime:{row['id']}:%"))
            )
        removed.append(row["id"])
    return removed
