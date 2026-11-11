import base64
import binascii
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agentbuddy.api.system import repository
from agentbuddy.catalog.capabilities import Capabilities
from agentbuddy.runtime.control import Control
from agentbuddy.runtime.files import Files, manifest

router = APIRouter()


@router.get("/projects/{project_id}/selection")
def project_selection(project_id: str, repo=Depends(repository)):
    from sqlalchemy import select

    from agentbuddy.persistence.schema import settings_table
    from agentbuddy.runtime.repository import RunRepository

    RunRepository(repo.db).project(project_id)
    with repo.db.engine.connect() as conn:
        row = (
            conn.execute(
                select(settings_table).where(settings_table.c.key == "selection:" + project_id)
            )
            .mappings()
            .first()
        )
    return {
        "data": {
            "selection": row["value_json"] if row else {},
            "revision": row["revision"] if row else 0,
        }
    }


class SelectionInput(BaseModel):
    selection: dict
    revision: int | None = None


@router.patch("/projects/{project_id}/selection")
def update_selection(project_id: str, body: SelectionInput, repo=Depends(repository)):
    from sqlalchemy import insert, select, update

    from agentbuddy.catalog.service import DomainError
    from agentbuddy.persistence.schema import settings_table
    from agentbuddy.runtime.repository import RunRepository

    RunRepository(repo.db).project(project_id)
    Capabilities(repo.db).resolve(body.selection)
    key = "selection:" + project_id
    with repo.db.write() as conn:
        old = conn.scalar(select(settings_table.c.revision).where(settings_table.c.key == key)) or 0
        if body.revision is not None and body.revision != old:
            raise DomainError("REVISION_CONFLICT", "会话能力已变化，请刷新")
        row = {"key": key, "revision": old + 1, "value_json": body.selection}
        conn.execute(
            update(settings_table).where(settings_table.c.key == key).values(**row)
            if old
            else insert(settings_table).values(**row)
        )
    return {"data": {"selection": body.selection, "revision": old + 1}}


class UploadInput(BaseModel):
    name: str
    skill_id: str | None = None
    content_base64: str = Field(max_length=70 * 1024 * 1024)
    draft_id: str = Field(min_length=1, max_length=128)


def decode(body):
    try:
        return base64.b64decode(body.content_base64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(422, "Invalid base64") from exc


def stage_upload(body, repo, kind):
    content = decode(body)
    limit = (50 if kind == "ingest_upload" else 20) * 1024 * 1024
    if len(content) > limit:
        raise HTTPException(413, "Upload too large")
    identifier = str(uuid4())
    folder = repo.db.path.parent / "staging"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / identifier).write_bytes(content)
    operation = repo.enqueue(
        kind,
        {
            "staged_id": identifier,
            "name": body.name,
            "draft_id": body.draft_id,
            "skill_id": body.skill_id,
        },
    )
    return {"id": operation["id"], "status": operation["status"]}


@router.post("/uploads", status_code=202)
def upload(body: UploadInput, repo=Depends(repository)):
    return {"data": stage_upload(body, repo, "ingest_upload")}


@router.post("/skills/import", status_code=202)
def install(body: UploadInput, repo=Depends(repository)):
    return {"data": stage_upload(body, repo, "skill_import")}


@router.post("/connectors/import", status_code=202)
def import_connector(body: UploadInput, repo=Depends(repository)):
    return {"data": stage_upload(body, repo, "connector_import")}


class CapabilityInput(BaseModel):
    id: str | None = None
    revision: int | None = None
    config: dict


@router.get("/capabilities/{kind}")
def capability_list(kind: str, repo=Depends(repository)):
    if kind not in ("skill", "expert", "connector"):
        raise HTTPException(404)
    return {"data": Capabilities(repo.db).list(kind)}


@router.post("/capabilities/{kind}")
def save(kind: str, body: CapabilityInput, repo=Depends(repository)):
    if kind not in ("skill", "expert", "connector"):
        raise HTTPException(404)
    if not body.config.get("name"):
        raise HTTPException(422, "name required")
    if kind == "skill":
        if not body.id:
            raise HTTPException(422, "Import a Skill package first")
        existing = Capabilities(repo.db).get(body.id)
        body.config = {**existing["config_json"], "enabled": bool(body.config.get("enabled"))}
    if kind == "connector" and body.config.get("transport") not in ("stdio", "http"):
        raise HTTPException(422, "transport must be stdio or http")
    return {"data": Capabilities(repo.db).save(kind, body.config, body.id, body.revision)}


@router.post("/capabilities/resolve/selection")
def resolve(body: dict, repo=Depends(repository)):
    return {"data": Capabilities(repo.db).resolve(body)}


@router.post("/connectors/{identifier}/test", status_code=202)
def test_connector(identifier: str, repo=Depends(repository)):
    row = Capabilities(repo.db).get(identifier)
    operation = repo.enqueue("connector_probe", row)
    return {"data": {"id": operation["id"], "status": operation["status"]}}


@router.get("/runs/{run_id}/control")
def control_state(run_id: str, repo=Depends(repository)):
    return {
        "data": {
            **Control(repo.db, run_id).state(),
            "interactions": Control(repo.db, run_id).pending(),
        }
    }


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, repo=Depends(repository)):
    return {"data": {"cancelled": Control(repo.db, run_id).cancel()}}


class Answer(BaseModel):
    digest: str
    approved: bool
    text: str | None = Field(default=None, max_length=32000)


@router.post("/runs/{run_id}/interactions/{identifier}")
def answer(run_id: str, identifier: str, body: Answer, repo=Depends(repository)):
    Control(repo.db, run_id).answer(identifier, body.digest, body.approved, body.text)
    return {"data": {"answered": True}}


@router.get("/projects/{project_id}/files")
def file_list(project_id: str, repo=Depends(repository)):
    rows = Files(repo.db).list(project_id)
    previous = {}
    for row in rows:
        if row["kind"] == "version":
            current = {f["path"]: f["sha256"] for f in row["manifest_json"]}
            row["diff"] = {
                "added": sorted(current.keys() - previous.keys()),
                "removed": sorted(previous.keys() - current.keys()),
                "modified": sorted(
                    p for p in current.keys() & previous.keys() if current[p] != previous[p]
                ),
            }
            previous = current
    return {"data": rows}


@router.get("/files/{identifier}/download")
def download(identifier: str, repo=Depends(repository)):
    return Response(
        Files(repo.db).archive(identifier),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{identifier}.zip"'},
    )


@router.get("/skills/{identifier}/versions")
def skill_versions(identifier: str, repo=Depends(repository)):
    registry = Capabilities(repo.db)
    current = registry.get(identifier)
    previous = [
        r["config_json"]["package"]
        for r in registry.list("skill_version")
        if r["config_json"]["skill_id"] == identifier
    ]
    packages = previous + [current["config_json"]]
    return {"data": list({p["package_id"]: p for p in packages}.values())}


class VersionSelection(BaseModel):
    package_id: str
    revision: int


@router.post("/skills/{identifier}/select-version")
def select_skill_version(identifier: str, body: VersionSelection, repo=Depends(repository)):
    registry = Capabilities(repo.db)
    current = registry.get(identifier)
    choices = skill_versions(identifier, repo)["data"]
    selected = next((v for v in choices if v["package_id"] == body.package_id), None)
    if not selected:
        raise HTTPException(404, "Skill version not found")
    registry.save("skill_version", {"skill_id": identifier, "package": current["config_json"]})
    return {
        "data": registry.save(
            "skill",
            {**selected, "enabled": current["config_json"]["enabled"]},
            identifier,
            body.revision,
        )
    }


@router.get("/runs/{run_id}/files")
def attempt_files(run_id: str, repo=Depends(repository)):
    from agentbuddy.runtime.repository import RunRepository

    run = RunRepository(repo.db).get(run_id)
    root = repo.db.path.parent / "projects" / run["project_id"] / "attempts" / run_id
    return {"data": {"status": run["status"], "files": manifest(root) if root.exists() else []}}


@router.get("/files/{identifier}/validation")
def validation_report(identifier: str, repo=Depends(repository)):
    import json

    from sqlalchemy import select

    from agentbuddy.persistence.schema import assets

    with repo.db.engine.connect() as conn:
        if not conn.scalar(
            select(assets.c.id).where(assets.c.id == identifier, assets.c.kind == "version")
        ):
            raise HTTPException(404, "Version not found")
    path = repo.db.path.parent / "versions" / (identifier + ".report.json")
    return {"data": json.loads(path.read_text(encoding="utf-8"))}


@router.get("/runs/{run_id}/activity")
def run_activity(run_id: str, repo=Depends(repository)):
    from sqlalchemy import select

    from agentbuddy.persistence.schema import run_events
    from agentbuddy.runtime.repository import RunRepository

    RunRepository(repo.db).get(run_id)
    with repo.db.engine.connect() as conn:
        rows = (
            conn.execute(
                select(run_events)
                .where(run_events.c.run_id == run_id, run_events.c.type != "message.delta")
                .order_by(run_events.c.id.desc())
                .limit(50)
            )
            .mappings()
            .all()
        )
    return {"data": [dict(r) for r in reversed(rows)]}


@router.get("/runs/{run_id}/files/download")
def download_attempt(run_id: str, repo=Depends(repository)):
    import io
    import zipfile

    from agentbuddy.runtime.repository import RunRepository

    run = RunRepository(repo.db).get(run_id)
    if run["status"] not in ("failed", "interrupted"):
        raise HTTPException(409, "Only stopped partial output can be downloaded here")
    root = repo.db.path.parent / "projects" / run["project_id"] / "attempts" / run_id
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in manifest(root):
            archive.write(root / item["path"], item["path"])
    return Response(
        output.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="partial-{run_id}.zip"'},
    )


@router.get("/runs/{run_id}/logs/{name}")
def download_log(run_id: str, name: str, repo=Depends(repository)):
    from fastapi.responses import FileResponse

    from agentbuddy.runtime.files import relative
    from agentbuddy.runtime.repository import RunRepository

    run = RunRepository(repo.db).get(run_id)
    if "/" in relative(name) or not name.endswith(".log"):
        raise HTTPException(422, "Invalid log name")
    path = repo.db.path.parent / "projects" / run["project_id"] / "logs" / name
    if not path.is_file():
        raise HTTPException(404, "Log not found")
    return FileResponse(path, filename=name, media_type="text/plain")


@router.get("/runs/{run_id}/tasks")
def run_tasks(run_id: str, repo=Depends(repository)):
    from sqlalchemy import select

    from agentbuddy.persistence.schema import subtasks
    from agentbuddy.runtime.repository import RunRepository

    RunRepository(repo.db).get(run_id)
    with repo.db.engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(select(subtasks).where(subtasks.c.run_id == run_id)).mappings()
        ]
    return {"data": rows}


@router.get("/office/dependencies")
def office_dependencies():
    from agentbuddy.runtime.office import FONT, renderer

    return {"data": {"renderer_available": bool(renderer()), "font": FONT}}


@router.get("/files/{asset_id}/content")
def file_content(asset_id: str, path: str, repo=Depends(repository)):
    from fastapi.responses import FileResponse
    from sqlalchemy import select

    from agentbuddy.catalog.service import DomainError
    from agentbuddy.persistence.schema import assets
    from agentbuddy.runtime.files import relative
    from agentbuddy.runtime.office import digest

    with repo.db.engine.connect() as conn:
        row = conn.execute(select(assets).where(assets.c.id == asset_id)).mappings().first()
    if not row:
        raise DomainError("NOT_FOUND", "文件版本不存在", 404)
    item = next((i for i in row["manifest_json"] if i["path"] == path), None)
    if not item:
        raise DomainError("NOT_FOUND", "文件不存在", 404)
    root = repo.db.path.parent / ("versions" if row["kind"] == "version" else "uploads") / asset_id
    target = root / relative(path)
    if not target.is_file() or digest(target) != item["sha256"]:
        raise DomainError("FILE_CHANGED", "文件完整性检查失败")
    text = target.suffix.lower() in {
        ".txt",
        ".md",
        ".json",
        ".csv",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".html",
        ".css",
    }
    preview = text or target.suffix.lower() in {".pdf", ".png"}
    return FileResponse(
        target, filename=target.name, content_disposition_type="inline" if preview else "attachment"
    )
