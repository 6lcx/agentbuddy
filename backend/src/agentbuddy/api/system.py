from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from agentbuddy.persistence.repository import QueueRepository


class WorkerStatus(BaseModel):
    id: str | None
    status: Literal["not_started", "running", "stale", "stopped", "lost"]
    heartbeat_at: float | None
    boot_id: str | None = None
    pid: int | None = None
    started_at: float | None = None


class StorageStatus(BaseModel):
    free_bytes: int
    total_bytes: int


class SystemStatus(BaseModel):
    database: Literal["ready"]
    worker: WorkerStatus
    storage: StorageStatus
    office_renderer_available: bool


class AcceptedOperation(BaseModel):
    id: str
    status: Literal["queued"]


class OperationResponse(BaseModel):
    id: str
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: float
    claimed_at: float | None
    finished_at: float | None
    result_json: dict | None
    error_json: dict | None


class EventResponse(BaseModel):
    id: int
    operation_id: str
    type: str
    payload_json: dict
    created_at: float


class EventPage(BaseModel):
    items: list[EventResponse]


router = APIRouter()


def repository(request: Request):
    database = request.app.state.database
    if not database.path.exists():
        raise HTTPException(503, "Database not initialized; run the migration command")
    try:
        database.require_schema()
    except (SQLAlchemyError, RuntimeError) as exc:
        raise HTTPException(503, "Database unavailable or migration required") from exc
    return QueueRepository(database)


Repo = Annotated[QueueRepository, Depends(repository)]


@router.get("/system/status", response_model=SystemStatus, tags=["system"])
def system_status(request: Request, repo: Repo):
    import shutil

    from agentbuddy.runtime.office import renderer

    disk = shutil.disk_usage(request.app.state.settings.data_dir)
    return {
        "storage": {"free_bytes": disk.free, "total_bytes": disk.total},
        "office_renderer_available": bool(renderer()),
        "database": "ready",
        "worker": repo.worker_status(request.app.state.settings.worker_stale_seconds),
    }


@router.post(
    "/system/diagnostics", status_code=202, response_model=AcceptedOperation, tags=["system"]
)
def create_diagnostic(repo: Repo):
    operation = repo.enqueue("storage_check", {})
    return {"id": operation["id"], "status": operation["status"]}


@router.get("/operations/{operation_id}", response_model=OperationResponse, tags=["operations"])
def get_operation(operation_id: str, repo: Repo):
    operation = repo.get(operation_id)
    if operation is None:
        raise HTTPException(404, "Operation not found")
    # Do not expose internal handler payloads/claim tokens in the public response.
    return {
        key: operation[key]
        for key in (
            "id",
            "kind",
            "status",
            "created_at",
            "claimed_at",
            "finished_at",
            "result_json",
            "error_json",
        )
    }


@router.get("/operations/{operation_id}/events", response_model=EventPage, tags=["operations"])
def get_events(
    operation_id: str,
    repo: Repo,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    if repo.get(operation_id) is None:
        raise HTTPException(404, "Operation not found")
    return {"items": repo.events(operation_id, after=after, limit=limit)}
