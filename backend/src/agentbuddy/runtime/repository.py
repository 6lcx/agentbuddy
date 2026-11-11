import hashlib
import json
import time
from uuid import uuid4

from sqlalchemy import func, insert, select, update

from agentbuddy.catalog.service import Catalog, DomainError
from agentbuddy.persistence.schema import (
    assets,
    messages,
    projects,
    request_keys,
    run_controls,
    run_events,
    runs,
    settings_table,
    workers,
)

TERMINAL = {"succeeded", "failed", "interrupted"}


class RunRepository:
    def __init__(self, db):
        self.db = db

    def _event(self, conn, run_id, kind, payload):
        conn.execute(
            insert(run_events).values(
                run_id=run_id, type=kind, payload_json=payload, created_at=time.time()
            )
        )

    def submit(self, payload, key):
        canonical = dict(payload)
        for field, default in {
            "calls_limit": 120,
            "seconds_limit": 1800,
            "selection": None,
            "upload_ids": [],
            "draft_id": None,
        }.items():
            if canonical.get(field) == default:
                canonical.pop(field, None)
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        with self.db.write() as conn:
            previous = (
                conn.execute(select(request_keys).where(request_keys.c.key == key))
                .mappings()
                .first()
            )
            if previous:
                if previous["request_hash"] != digest:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "相同请求标识对应了不同内容")
                return previous["response_json"]
            catalog = Catalog(self.db)
            model_key = payload.get("model_id") or catalog.settings(conn)["default_model_id"]
            from agentbuddy.catalog.capabilities import Capabilities

            selection = payload.get("selection")
            if selection is None and payload.get("project_id"):
                selection = conn.scalar(
                    select(settings_table.c.value_json).where(
                        settings_table.c.key == "selection:" + payload["project_id"]
                    )
                )
            resolved = Capabilities(self.db).resolve(selection or {}, conn)
            if resolved["expert"] and not payload.get("model_id"):
                model_key = resolved["expert"]["config_json"].get("model_id") or model_key
            snapshot = catalog.snapshot(conn, model_key)
            snapshot["capabilities"] = resolved
            snapshot["upload_ids"] = payload.get("upload_ids", [])
            mode = (selection or {}).get("mode") or ("expert" if resolved["expert"] else "general")
            if mode not in ("general", "expert", "team"):
                raise DomainError("INVALID_MODE", "运行模式不正确", 422)
            snapshot["mode"] = mode
            candidate_ids = []
            registry = Capabilities(self.db)
            if mode == "team":
                candidate_ids = [
                    r["id"]
                    for r in registry.list("expert")
                    if r["config_json"].get("enabled", True)
                ]
            elif mode == "expert" and resolved["expert"]:
                candidate_ids = resolved["expert"]["config_json"].get("collaborator_ids", [])
            snapshot["candidates"] = []
            for identifier in dict.fromkeys(candidate_ids):
                own = registry.resolve({"expert_id": identifier}, conn)
                own_model = (
                    own["expert"]["config_json"].get("model_id")
                    or catalog.settings(conn)["default_model_id"]
                )
                child = catalog.snapshot(conn, own_model)
                child["capabilities"] = own
                snapshot["candidates"].append(child)
            if mode == "team" and not snapshot["candidates"]:
                raise DomainError("MISSING_EXPERTS", "请先启用至少一个专家", 422)
            project_id = payload.get("project_id")
            now = time.time()
            if project_id:
                if not conn.scalar(select(projects.c.id).where(projects.c.id == project_id)):
                    raise DomainError("NOT_FOUND", "对话不存在", 404)
                if conn.scalar(
                    select(runs.c.id).where(
                        runs.c.project_id == project_id, runs.c.status.in_(["queued", "running"])
                    )
                ):
                    raise DomainError("ACTIVE_RUN", "本对话已有任务执行中，请等待完成")
            else:
                project_id = str(uuid4())
                conn.execute(
                    insert(projects).values(
                        id=project_id, title=payload["message"][:60], created_at=now, updated_at=now
                    )
                )
            selection_key = "selection:" + project_id
            selected = conn.scalar(
                select(settings_table.c.revision).where(settings_table.c.key == selection_key)
            )
            if selected is None:
                conn.execute(
                    insert(settings_table).values(
                        key=selection_key, value_json=resolved["selection"], revision=1
                    )
                )
            else:
                conn.execute(
                    update(settings_table)
                    .where(settings_table.c.key == selection_key)
                    .values(value_json=resolved["selection"], revision=selected + 1)
                )
            run_id = str(uuid4())
            conn.execute(
                insert(runs).values(
                    id=run_id,
                    project_id=project_id,
                    status="queued",
                    snapshot_json=snapshot,
                    partial_text="",
                    created_at=now,
                )
            )
            conn.execute(
                insert(run_controls).values(
                    run_id=run_id,
                    calls_limit=payload.get("calls_limit", 120),
                    calls_used=0,
                    seconds_limit=payload.get("seconds_limit", 1800),
                    elapsed=0,
                    active_since=None,
                )
            )
            total_size = 0
            for upload_id in snapshot["upload_ids"]:
                upload = (
                    conn.execute(
                        select(assets).where(assets.c.id == upload_id, assets.c.kind == "input")
                    )
                    .mappings()
                    .first()
                )
                if (
                    not upload
                    or upload["project_id"] not in (None, project_id)
                    or (
                        upload["project_id"] is None
                        and upload["draft_id"] != payload.get("draft_id")
                    )
                ):
                    raise DomainError("UPLOAD_OWNERSHIP", "附件不属于当前草稿或项目")
                total_size += sum(f["size"] for f in upload["manifest_json"])
                if total_size > 200 * 1024 * 1024:
                    raise DomainError("UPLOAD_LIMIT", "本次附件合计超过 200 MiB")
                conn.execute(
                    update(assets).where(assets.c.id == upload_id).values(project_id=project_id)
                )
            conn.execute(
                insert(messages).values(
                    id=str(uuid4()),
                    project_id=project_id,
                    run_id=run_id,
                    role="user",
                    content=payload["message"],
                    created_at=now,
                )
            )
            conn.execute(update(projects).where(projects.c.id == project_id).values(updated_at=now))
            response = {"project_id": project_id, "run_id": run_id}
            conn.execute(
                insert(request_keys).values(key=key, request_hash=digest, response_json=response)
            )
            self._event(conn, run_id, "run.status", {"status": "queued"})
            return response

    def project_list(self):
        with self.db.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(projects).order_by(projects.c.updated_at.desc())
                ).mappings()
            ]

    def project(self, project_id):
        with self.db.engine.connect() as conn:
            row = (
                conn.execute(select(projects).where(projects.c.id == project_id)).mappings().first()
            )
            if not row:
                raise DomainError("NOT_FOUND", "对话不存在", 404)
            return dict(row)

    def history(self, project_id):
        with self.db.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(messages)
                    .where(messages.c.project_id == project_id)
                    .order_by(messages.c.created_at, messages.c.id)
                ).mappings()
            ]

    def project_runs(self, project_id):
        with self.db.engine.connect() as conn:
            return [
                self.public(r)
                for r in conn.execute(
                    select(runs)
                    .where(runs.c.project_id == project_id)
                    .order_by(runs.c.created_at.desc())
                ).mappings()
            ]

    def get(self, run_id):
        with self.db.engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
            if not row:
                raise DomainError("NOT_FOUND", "任务不存在", 404)
            return dict(row)

    def public(self, row):
        result = {
            key: row[key]
            for key in (
                "id",
                "project_id",
                "status",
                "partial_text",
                "failure_json",
                "created_at",
                "finished_at",
            )
        }

        from agentbuddy.runtime.control import Control

        if row["failure_json"] and row["failure_json"].get("code") == "cancelled":
            result["status"] = "cancelled"
        elif (
            row["status"] == "running"
            and Control(self.db, row["id"]).state()["active_since"] is None
        ) and any(i["status"] == "pending" for i in Control(self.db, row["id"]).pending()):
            result["status"] = "waiting_input"
        return result

    def events(self, run_id, after=0):
        with self.db.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(run_events)
                    .where(run_events.c.run_id == run_id, run_events.c.id > after)
                    .order_by(run_events.c.id)
                    .limit(100)
                ).mappings()
            ]

    def claim(self, worker_id):
        with self.db.write() as conn:
            if (
                conn.scalar(
                    select(func.count())
                    .select_from(runs.join(run_controls))
                    .where(runs.c.status == "running", run_controls.c.active_since.is_not(None))
                )
                >= 2
            ):
                return None
            if conn.scalar(select(workers.c.status).where(workers.c.id == worker_id)) != "running":
                return None
            row = (
                conn.execute(
                    select(runs)
                    .where(runs.c.status == "queued")
                    .order_by(runs.c.created_at)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            claimed = dict(row) | {
                "status": "running",
                "worker_id": worker_id,
                "claim_token": str(uuid4()),
            }
            conn.execute(update(runs).where(runs.c.id == row["id"]).values(**claimed))
            conn.execute(
                update(run_controls)
                .where(run_controls.c.run_id == row["id"])
                .values(active_since=time.time())
            )
            self._event(conn, row["id"], "run.status", {"status": "running"})
            return claimed

    def _owned(self, claimed):
        return (
            runs.c.id == claimed["id"],
            runs.c.status == "running",
            runs.c.claim_token == claimed["claim_token"],
            runs.c.worker_id == claimed["worker_id"],
        )

    def append(self, claimed, text):
        with self.db.write() as conn:
            changed = conn.execute(
                update(runs)
                .where(*self._owned(claimed))
                .values(partial_text=runs.c.partial_text + text)
            ).rowcount
            if changed:
                self._event(conn, claimed["id"], "message.delta", {"text": text})
            return bool(changed)

    def finish(self, claimed, text="", error=None, version=None):
        now = time.time()
        status = "failed" if error else "succeeded"
        with self.db.write() as conn:
            changed = conn.execute(
                update(runs)
                .where(*self._owned(claimed))
                .values(status=status, failure_json=error, finished_at=now)
            ).rowcount
            if not changed:
                return False
            from agentbuddy.runtime.control import Control

            Control(self.db, claimed["id"]).settle(conn)
            if not error:
                if version:
                    conn.execute(insert(assets).values(**version))
                conn.execute(
                    insert(messages).values(
                        id=str(uuid4()),
                        run_id=claimed["id"],
                        project_id=claimed["project_id"],
                        role="assistant",
                        content=text,
                        created_at=now,
                    )
                )
                conn.execute(
                    update(runs).where(runs.c.id == claimed["id"]).values(partial_text=text)
                )
                self._event(conn, claimed["id"], "message.completed", {"text": text})
            self._event(conn, claimed["id"], "run.completed", {"status": status, "error": error})
            return True

    def interrupt(self, worker_id=None):
        with self.db.write() as conn:
            query = select(runs.c.id).where(runs.c.status == "running")
            if worker_id:
                query = query.where(runs.c.worker_id == worker_id)
            for run_id in conn.scalars(query).all():
                from agentbuddy.runtime.control import Control

                Control(self.db, run_id).settle(conn)
                from agentbuddy.persistence.schema import subtasks

                conn.execute(
                    update(subtasks)
                    .where(
                        subtasks.c.run_id == run_id, subtasks.c.status.in_(["queued", "running"])
                    )
                    .values(status="interrupted", finished_at=time.time())
                )
                error = {"code": "worker_interrupted", "message": "执行进程已停止，请重新发送任务"}
                conn.execute(
                    update(runs)
                    .where(runs.c.id == run_id)
                    .values(status="interrupted", failure_json=error, finished_at=time.time())
                )
                self._event(
                    conn, run_id, "run.completed", {"status": "interrupted", "error": error}
                )
