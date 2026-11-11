import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agentbuddy.api.contracts import Data, Listing, MessageView, ProjectView, RunAccepted, RunView
from agentbuddy.api.system import repository
from agentbuddy.runtime.agent_policy import AgentPolicy
from agentbuddy.runtime.context_policy import ContextPolicy
from agentbuddy.runtime.repository import TERMINAL, RunRepository

router = APIRouter()


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calls_limit: int = Field(default=120, ge=1, le=500)
    seconds_limit: int = Field(default=1800, ge=1, le=7200)
    selection: dict | None = None
    upload_ids: list[str] = Field(default_factory=list, max_length=100)
    draft_id: str | None = None
    project_id: str | None = None
    model_id: str | None = None
    message: str = Field(min_length=1, max_length=32000)
    context_policy: ContextPolicy | None = None
    agent_policy: AgentPolicy | None = None


def run_repository(repo=Depends(repository)):
    return RunRepository(repo.db)


Runs = Annotated[RunRepository, Depends(run_repository)]


@router.post("/runs", status_code=202, response_model=Data[RunAccepted])
def submit_run(
    body: RunInput,
    service: Runs,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    if not body.message.strip():
        raise HTTPException(422, "请输入消息")
    return {"data": service.submit(body.model_dump(exclude_unset=True), idempotency_key)}


@router.get("/projects", response_model=Listing[ProjectView])
def project_list(service: Runs):
    return {"data": service.project_list(), "next_cursor": None}


@router.get("/projects/{project_id}", response_model=Data[ProjectView])
def project_get(project_id: str, service: Runs):
    return {"data": service.project(project_id)}


@router.get("/projects/{project_id}/messages", response_model=Listing[MessageView])
def history(project_id: str, service: Runs):
    service.project(project_id)
    return {"data": service.history(project_id), "next_cursor": None}


@router.get("/projects/{project_id}/runs", response_model=Listing[RunView])
def project_runs(project_id: str, service: Runs):
    service.project(project_id)
    return {"data": service.project_runs(project_id), "next_cursor": None}


@router.get("/runs/{run_id}", response_model=Data[RunView])
def get_run(run_id: str, service: Runs):
    return {"data": service.public(service.get(run_id))}


@router.get("/runs/{run_id}/checkpoint")
def checkpoint(run_id: str, service: Runs):
    from agentbuddy.runtime.checkpoints import checkpoint_status

    return {"data": checkpoint_status(service.db, service.get(run_id))}


@router.post("/runs/{run_id}/resume", status_code=202)
def resume(run_id: str, service: Runs):
    from agentbuddy.runtime.checkpoints import resume_run

    return {"data": resume_run(service.db, service.get(run_id))}


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    service: Runs,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header()] = None,
):
    await asyncio.to_thread(service.get, run_id)
    try:
        cursor = max(after, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(422, "Invalid Last-Event-ID") from exc
    if cursor < 0:
        raise HTTPException(422, "Invalid event cursor")

    async def generate():
        nonlocal cursor
        idle = 0
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(service.events, run_id, cursor)
            for row in rows:
                cursor = row["id"]
                payload = {
                    "id": row["id"],
                    "run_id": run_id,
                    "type": row["type"],
                    "occurred_at": row["created_at"],
                    "payload": row["payload_json"],
                }
                data = json.dumps(payload, ensure_ascii=False)
                yield f"id: {cursor}\nevent: {row['type']}\ndata: {data}\n\n"
            # Check status BEFORE the final drain: commits include the terminal event atomically.
            current = await asyncio.to_thread(service.get, run_id)
            if current["status"] in TERMINAL:
                if await asyncio.to_thread(service.events, run_id, cursor):
                    continue
                break
            idle += 1
            if idle % 20 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
