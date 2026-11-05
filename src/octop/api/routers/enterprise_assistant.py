"""Opt-in enterprise assistant configuration, evidence and owner confirmation."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from octop.api.deps import current_user, get_server
from octop.infra.enterprise_assistant.adapters import WorkflowError
from octop.infra.enterprise_assistant.models import (
    ConnectionInput,
    Decision,
    DraftEdit,
    Features,
    JobInput,
)
from octop.infra.enterprise_assistant.service import AssistantService

router = APIRouter(prefix="/enterprise-assistant", tags=["enterprise-assistant"])


class ResourceRecord(BaseModel):
    id: str
    owner_id: int
    category: str
    version: int
    config: dict[str, Any]
    has_secret: bool
    next_due: int | None
    created_at: int
    updated_at: int


class RunRecord(BaseModel):
    id: str
    owner_id: int
    job_id: str
    trigger_key: str
    status: str
    version: int
    snapshot: dict[str, Any]
    evidence: list[dict[str, Any]]
    content: str
    content_hash: str
    error_code: str
    created_at: int
    updated_at: int


def service(
    server: Any = Depends(get_server), user: Any = Depends(current_user)
) -> Iterator[AssistantService]:
    if server.services is None:
        raise HTTPException(503, detail="DATABASE_NOT_READY")
    try:
        yield AssistantService(server.services.repos, user)
    except WorkflowError as exc:
        code = str(exc)
        status = 404 if code == "RESOURCE_NOT_FOUND" else 409
        if code in {"ACCESS_DENIED", "ADMIN_REQUIRED", "MODULE_DISABLED", "AGENT_NOT_AVAILABLE"}:
            status = 403
        raise HTTPException(status, detail=code) from exc


@router.get("/features", response_model=Features, summary="Get enterprise module switches")
def features(svc: AssistantService = Depends(service)) -> Features:
    return svc.flags()


@router.put(
    "/features",
    response_model=Features,
    summary="Admin: enable modules and approve network origins",
)
def configure_features(body: Features, svc: AssistantService = Depends(service)) -> Features:
    return svc.set_flags(body)


@router.get(
    "/connections", response_model=list[ResourceRecord], summary="List own encrypted connections"
)
def connections(svc: AssistantService = Depends(service)) -> list[dict[str, Any]]:
    return svc.list_resources("connection")


@router.post(
    "/connections", response_model=ResourceRecord, summary="Create an owner-scoped connection"
)
def create_connection(
    body: ConnectionInput, svc: AssistantService = Depends(service)
) -> dict[str, Any]:
    return svc.save_connection(body)


@router.put(
    "/connections/{resource_id}",
    response_model=ResourceRecord,
    summary="Update connection using version check",
)
def update_connection(
    resource_id: str, body: ConnectionInput, svc: AssistantService = Depends(service)
) -> dict[str, Any]:
    return svc.save_connection(body, resource_id)


@router.get("/jobs", response_model=list[ResourceRecord], summary="List own Agent-bound jobs")
def jobs(svc: AssistantService = Depends(service)) -> list[dict[str, Any]]:
    return svc.list_resources("job")


@router.post(
    "/jobs",
    response_model=ResourceRecord,
    summary="Create a scheduled collection or confirmation workflow",
)
def create_job(body: JobInput, svc: AssistantService = Depends(service)) -> dict[str, Any]:
    return svc.save_job(body)


@router.put(
    "/jobs/{resource_id}",
    response_model=ResourceRecord,
    summary="Update job and recompute next schedule",
)
def update_job(
    resource_id: str, body: JobInput, svc: AssistantService = Depends(service)
) -> dict[str, Any]:
    return svc.save_job(body, resource_id)


@router.post(
    "/jobs/{resource_id}/runs",
    response_model=RunRecord,
    summary="Request execution; writes wait for human confirmation",
)
def execute_job(resource_id: str, svc: AssistantService = Depends(service)) -> dict[str, Any]:
    return svc.enqueue(resource_id)


@router.get(
    "/runs",
    response_model=list[RunRecord],
    summary="List latest execution evidence and pending confirmations",
)
def runs(svc: AssistantService = Depends(service)) -> list[dict[str, Any]]:
    return svc.list_runs()


@router.put(
    "/runs/{run_id}/draft",
    response_model=RunRecord,
    summary="Edit pending report; invalidate earlier confirmation hash",
)
def edit_draft(
    run_id: str, body: DraftEdit, svc: AssistantService = Depends(service)
) -> dict[str, Any]:
    return svc.edit_draft(run_id, body.expected_version, body.content)


@router.post(
    "/runs/{run_id}/decision",
    response_model=RunRecord,
    summary="Owner: confirm exact payload and destination, or reject",
)
def decision(
    run_id: str, body: Decision, svc: AssistantService = Depends(service)
) -> dict[str, Any]:
    return svc.decide(run_id, body)
