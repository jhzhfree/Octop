"""Enterprise scene configuration, run requests, and human approval API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from octop.api.common.agent import require_agent_row, user_owns_agent
from octop.api.deps import current_user, get_server
from octop.infra.scenes.service import EnterpriseSceneService, SceneError

router = APIRouter()


class SceneCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    position_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=20000)
    mcp_servers: list[str] = Field(default_factory=list, max_length=50)


class ScenePatchBody(BaseModel):
    expected_version: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    mcp_servers: list[str] | None = Field(default=None, max_length=50)
    enabled: bool | None = None


class SceneDecisionBody(BaseModel):
    note: str = Field(default="", max_length=2000)


class SceneRunRequestBody(BaseModel):
    plan_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Ready digital-worker plan required for new run requests; omission returns SCENE_PLAN_REQUIRED.",
    )
    use_memory: bool = True
    capture_memory: bool = True
    inspection_job_ids: list[str] = Field(default_factory=list, max_length=10)
    share_inspection_results: bool = False


class SceneRecord(BaseModel):
    scene_id: str
    organization_id: str
    position_id: str
    agent_id: str
    owner_user_id: int
    version: int
    name: str
    prompt: str
    mcp_servers: list[str]
    enabled: bool | int
    created_at: int
    updated_at: int


class SceneRunRecord(BaseModel):
    run_id: str
    scene_id: str
    organization_id: str
    position_id: str
    agent_id: str
    scene_name: str
    scene_version: int
    prompt_snapshot: str
    mcp_servers: list[str]
    requested_by: int
    status: Literal["awaiting_approval", "rejected", "queued", "succeeded", "failed"]
    approved_by: int | None
    decision_note: str
    cron_id: str | None
    error_summary: str | None
    plan_id: str | None = None
    plan_snapshot: dict[str, Any] | None = None
    created_at: int
    updated_at: int


class SceneRunEvidenceRecord(BaseModel):
    run_id: str
    available: bool
    answer: str | None


class OperationTraceEvent(BaseModel):
    record_id: int
    recorded_at: int
    action: Literal[
        "scene.run_requested",
        "scene.run_approved",
        "scene.run_rejected",
        "scene.run.delivery_reviewed",
        "scene.mattermost_publication.proposed",
        "scene.mattermost_publication.approved_locally",
        "scene.mattermost_publication.revoked",
        "cron.run_ok",
        "cron.run_failed",
    ]
    actor_kind: Literal["user_account", "system", "unknown"]
    actor_user_id: int | None
    interaction_mode: Literal["service_execution", "not_recorded"] = Field(
        description="Account actions do not prove a human clicked; legacy interaction channels were not recorded."
    )


class OperationExecutionContext(BaseModel):
    agent_id: str
    on_behalf_of_user_id: int
    bound_cron_id: str | None
    configured_executor: Literal["http_inspection", "agent_task", "unknown"]
    schedule_kind: Literal["one_shot", "unknown"]


class OperationTraceRecord(BaseModel):
    organization_id: str
    run_id: str
    as_of: int
    events: list[OperationTraceEvent]
    truncated: bool
    history_complete: Literal[False]
    execution_authorized: Literal[False]
    execution_context: OperationExecutionContext


class SceneVersionRecord(BaseModel):
    scene_id: str
    version: int
    name: str
    prompt: str
    mcp_servers: list[str]
    enabled: bool | int
    created_by: int
    created_at: int


def _service(server: Any, user: Any) -> EnterpriseSceneService:
    if server.services is None:
        raise HTTPException(status_code=503, detail="database is not ready")
    return EnterpriseSceneService(server.services.repos, user)


def _call[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except SceneError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        ) from exc


@router.get(
    "/organizations/{organization_id}/scenes",
    summary="List enterprise scenes",
    response_model=list[SceneRecord],
)
async def list_scenes(
    organization_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    return _call(lambda: _service(server, user).list_scenes(organization_id))


@router.post(
    "/organizations/{organization_id}/scenes",
    status_code=201,
    summary="Create a position-bound enterprise scene",
    response_model=SceneRecord,
)
async def create_scene(
    organization_id: str,
    body: SceneCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    from octop.api.common.validators import validate_chat_mcp_servers  # noqa: PLC0415

    agent = require_agent_row(body.agent_id, user=user, as_user=None, server=server)
    if not user_owns_agent(agent, user):
        raise HTTPException(status_code=403, detail="Agent owner access required")
    mcp_servers = (
        await validate_chat_mcp_servers(server, user_id=user.id, names=body.mcp_servers) or []
    )
    service = _service(server, user)
    return _call(
        lambda: service.create_scene(
            organization_id,
            position_id=body.position_id,
            agent_id=body.agent_id,
            name=body.name,
            prompt=body.prompt,
            mcp_servers=mcp_servers,
        )
    )


@router.patch(
    "/organizations/{organization_id}/scenes/{scene_id}",
    summary="Update an enterprise scene",
    response_model=SceneRecord,
)
async def update_scene(
    organization_id: str,
    scene_id: str,
    body: ScenePatchBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    from octop.api.common.validators import validate_chat_mcp_servers  # noqa: PLC0415

    service = _service(server, user)
    updates = body.model_dump(exclude_unset=True)
    expected_version = updates.pop("expected_version")
    if "mcp_servers" in updates:
        updates["mcp_servers"] = (
            await validate_chat_mcp_servers(server, user_id=user.id, names=body.mcp_servers or [])
            or []
        )
    return _call(
        lambda: service.update_scene(
            organization_id, scene_id, updates, expected_version=expected_version
        )
    )


@router.get(
    "/organizations/{organization_id}/scenes/{scene_id}/versions",
    summary="List immutable enterprise scene versions",
    response_model=list[SceneVersionRecord],
)
async def list_scene_versions(
    organization_id: str,
    scene_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    return _call(lambda: _service(server, user).list_scene_versions(organization_id, scene_id))


@router.post(
    "/organizations/{organization_id}/scenes/{scene_id}/runs",
    status_code=201,
    summary="Request an enterprise scene run",
    description="Requires a ready plan belonging to the scene worker. Legacy planless requests remain readable and rejectable, but cannot be approved.",
    response_model=SceneRunRecord,
)
async def request_scene_run(
    organization_id: str,
    scene_id: str,
    body: SceneRunRequestBody | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    from octop.api.common.validators import validate_chat_mcp_servers  # noqa: PLC0415

    service = _service(server, user)
    scene = _call(lambda: service.get_scene(organization_id, scene_id))
    authorized = (
        await validate_chat_mcp_servers(server, user_id=user.id, names=list(scene["mcp_servers"]))
        or []
    )
    if sorted(authorized) != sorted(scene["mcp_servers"]):
        raise HTTPException(status_code=403, detail="Scene connector access is not granted")
    return _call(
        lambda: service.request_run(
            organization_id,
            scene_id,
            plan_id=body.plan_id if body is not None else None,
            use_memory=body.use_memory if body is not None else True,
            capture_memory=body.capture_memory if body is not None else True,
            inspection_job_ids=body.inspection_job_ids if body is not None else [],
            share_inspection_results=body.share_inspection_results if body is not None else False,
        )
    )


@router.get(
    "/organizations/{organization_id}/scene-runs",
    summary="List enterprise scene runs",
    response_model=list[SceneRunRecord],
)
async def list_scene_runs(
    organization_id: str,
    scene_id: str | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    return _call(lambda: _service(server, user).list_runs(organization_id, scene_id=scene_id))


@router.get(
    "/organizations/{organization_id}/scene-runs/{run_id}",
    summary="Get enterprise scene run details",
    response_model=SceneRunRecord,
)
async def get_scene_run(
    organization_id: str,
    run_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _call(lambda: _service(server, user).get_run(organization_id, run_id))


@router.get(
    "/organizations/{organization_id}/scene-runs/{run_id}/evidence",
    summary="Read the visible result of an enterprise scene run",
    description="Rechecks current identity, task access and run binding after asynchronous reads. Native inspection reports must match persisted content hashes. Does not accept or send delivery.",
    response_model=SceneRunEvidenceRecord,
)
async def get_scene_run_evidence(
    organization_id: str,
    run_id: str,
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    from octop.infra.scenes.delivery_evidence import SceneDeliveryEvidenceService
    from octop.infra.scenes.evidence import scene_run_evidence

    response.headers["Cache-Control"] = "no-store"

    async def load(run: dict[str, Any]) -> str | None:
        runtime = server.app_runtime
        return await scene_run_evidence(
            run=run,
            cron_manager=runtime.cron_manager,
            thread_registry=runtime.gateway.thread_registry,
            agent_registry=runtime.agent_registry,
            thread_message_repo=server.services.thread_message_repo,
            history_archive=getattr(runtime, "history_archive", None),
        )

    try:
        return await SceneDeliveryEvidenceService(_service(server, user), load).read(
            organization_id, run_id
        )
    except SceneError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
            headers={"Cache-Control": "no-store"},
        ) from exc


@router.get(
    "/organizations/{organization_id}/scene-runs/{run_id}/operation-trace",
    summary="Read recorded operation accounts and bound execution context",
    description="Current authorized run participants or administrators only. Latest 100 allowlisted audit records, newest inserted first; no raw payloads. Missing/retained history is not reconstructed. Does not reconcile, execute, approve or send anything.",
    response_model=OperationTraceRecord,
)
def get_scene_operation_trace(
    organization_id: str,
    run_id: str,
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    from octop.infra.scenes.operation_trace import SceneOperationTraceService

    response.headers["Cache-Control"] = "no-store"
    try:
        return SceneOperationTraceService(_service(server, user)).read(organization_id, run_id)
    except SceneError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
            headers={"Cache-Control": "no-store"},
        ) from exc


@router.post(
    "/organizations/{organization_id}/scene-runs/{run_id}/approve",
    summary="Approve and queue an enterprise scene run",
    response_model=SceneRunRecord,
)
async def approve_scene_run(
    organization_id: str,
    run_id: str,
    body: SceneDecisionBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return await _service(server, user).decide_run(
            organization_id, run_id, approve=True, note=body.note, server=server
        )
    except SceneError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        ) from exc


@router.post(
    "/organizations/{organization_id}/scene-runs/{run_id}/reject",
    summary="Reject an enterprise scene run",
    response_model=SceneRunRecord,
)
async def reject_scene_run(
    organization_id: str,
    run_id: str,
    body: SceneDecisionBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return await _service(server, user).decide_run(
            organization_id, run_id, approve=False, note=body.note, server=server
        )
    except SceneError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        ) from exc


@router.get(
    "/organizations/{organization_id}/scenes/{scene_id}/inspection-jobs",
    summary="List own HTTP inspection candidates without connection details",
)
async def inspection_jobs(
    organization_id: str,
    scene_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    from octop.infra.enterprise_assistant.adapters import WorkflowError
    from octop.infra.scenes.inspection import candidates

    service = _service(server, user)
    scene = _call(lambda: service.get_scene(organization_id, scene_id))
    try:
        return candidates(service.repos, user, str(scene["agent_id"]))
    except WorkflowError as exc:
        raise HTTPException(status_code=403, detail="Inspection access unavailable") from exc


@router.get(
    "/organizations/{organization_id}/scene-runs/{run_id}/inspection-result",
    summary="Read same-run inspection counts and source identities without connection data",
)
async def get_inspection_result(
    organization_id: str,
    run_id: str,
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any] | None:
    from octop.infra.db.repos.scene_acceptance import AcceptanceStoreError
    from octop.infra.scenes.inspection import inspection_result

    response.headers["Cache-Control"] = "no-store"
    service = _service(server, user)
    try:
        return inspection_result(service.repos, user, organization_id, run_id)
    except AcceptanceStoreError as exc:
        raise HTTPException(status_code=403, detail="Inspection result access denied") from exc
