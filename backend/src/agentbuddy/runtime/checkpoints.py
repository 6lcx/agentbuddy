"""Resume the same run at a durable model boundary, never replay a dirty tool."""

from sqlalchemy import select, update

from agentbuddy.catalog.service import DomainError
from agentbuddy.persistence.schema import run_controls, run_lanes, runs
from agentbuddy.providers.native import ProviderFailure
from agentbuddy.runtime.files import Files, manifest
from agentbuddy.runtime.runtime_store import RuntimeStore, fingerprint


def checkpoint_status(db, run):
    store = RuntimeStore(db, run["project_id"], run["id"])
    checkpoint = store.get("checkpoint", {})
    return {
        "available": bool(checkpoint),
        "safe": checkpoint.get("safe", False),
        "boundary": "before_model",
        "pending_tool": checkpoint.get("pending_tool"),
        "resumable": bool(checkpoint.get("safe")) and run["status"] == "interrupted",
    }


def resume_run(db, run):
    store = RuntimeStore(db, run["project_id"], run["id"])
    checkpoint = store.get("checkpoint", {})
    if run["status"] != "interrupted" or not checkpoint.get("safe"):
        raise DomainError(
            "CHECKPOINT_UNSAFE", "没有可安全恢复的中断边界；请核对外部操作并重新提交", 409
        )
    if checkpoint.get("version") != 1:
        raise DomainError("CHECKPOINT_VERSION", "检查点版本不兼容", 409)

    def verify_references(value):
        if isinstance(value, dict):
            if "result_id" in value and "sha256" in value:
                try:
                    store.read(value["result_id"], limit=1)
                except (ValueError, OSError, ProviderFailure) as exc:
                    raise DomainError(
                        "CHECKPOINT_RESULT_CHANGED", "检查点结果引用缺失或损坏", 409
                    ) from exc
            for child in value.values():
                verify_references(child)
        elif isinstance(value, list):
            for child in value:
                verify_references(child)

    for identifier in checkpoint.get("result_ids", []):
        verify_references({"result_id": identifier, "sha256": identifier})
    loaded = set(checkpoint.get("loaded", []))
    for skill in run["snapshot_json"].get("capabilities", {}).get("skills", []):
        if skill["id"] in loaded:
            config = skill["config_json"]
            package = db.path.parent / "skills" / config["package_id"]
            if manifest(package) != config["files"]:
                raise DomainError("CHECKPOINT_SKILL_CHANGED", "检查点引用的Skill包已变化", 409)
    verify_references(checkpoint.get("context", {}))
    verify_references(store.get("loaded_resources", {}))
    if checkpoint.get("snapshot_hash") != fingerprint(run["snapshot_json"]):
        raise DomainError("CHECKPOINT_CONFIG_CHANGED", "配置快照不匹配", 409)
    if manifest(Files(db).workspace(run)) != checkpoint["files"]:
        raise DomainError("CHECKPOINT_FILES_CHANGED", "检查点之后文件发生变化，不能自动恢复", 409)
    with db.write() as conn:
        if conn.scalar(
            select(runs.c.id).where(
                runs.c.project_id == run["project_id"], runs.c.created_at > run["created_at"]
            )
        ):
            raise DomainError("CHECKPOINT_STALE", "项目已有后续轮次，请从最新版本继续", 409)
        if conn.scalar(
            select(runs.c.id).where(
                runs.c.project_id == run["project_id"], runs.c.status.in_(["queued", "running"])
            )
        ):
            raise DomainError("PROJECT_BUSY", "项目已有在途任务", 409)
        budget = (
            conn.execute(select(run_controls).where(run_controls.c.run_id == run["id"]))
            .mappings()
            .one()
        )
        if (
            budget["calls_used"] >= budget["calls_limit"]
            or budget["elapsed"] >= budget["seconds_limit"]
        ):
            raise DomainError("BUDGET_EXHAUSTED", "恢复不会重置已耗尽的预算", 409)
        changed = conn.execute(
            update(runs)
            .where(runs.c.id == run["id"], runs.c.status == "interrupted")
            .values(status="queued", failure_json=None, finished_at=None, partial_text="")
        ).rowcount
        if not changed:
            raise DomainError("CHECKPOINT_CONFLICT", "任务状态已变化", 409)
        conn.execute(update(run_lanes).where(run_lanes.c.run_id == run["id"]).values(state="done"))
        from agentbuddy.runtime.repository import RunRepository

        RunRepository(db)._event(
            conn, run["id"], "checkpoint.resume_requested", {"budget_preserved": True}
        )
    return {"run_id": run["id"], "project_id": run["project_id"]}
