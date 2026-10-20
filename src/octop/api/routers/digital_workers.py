"""Virtual worker profiles and stage goals."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server
from octop.infra.connectors.catalog import get_catalog_entry
from octop.infra.connectors.probe import probe_connector
from octop.infra.connectors.service import ConnectorService
from octop.infra.digital_workers import DigitalWorkerError, DigitalWorkerService
from octop.infra.digital_workers.memory import WorkerMemoryService
from octop.infra.knowledge.diagnostics import credential_rotation_state

router = APIRouter()


class DigitalWorkerCreateBody(BaseModel):
    position_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    responsibilities: str | None = Field(default=None, max_length=8000)


class DigitalWorkerPatchBody(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    responsibilities: str | None = Field(default=None, max_length=8000)
    status: Literal["draft", "active", "paused", "archived"] | None = None


class WorkerGoalCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    success_criteria: str = Field(min_length=1, max_length=4000)
    period_start: date | None = None
    period_end: date | None = None


class WorkerGoalPatchBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    success_criteria: str | None = Field(default=None, min_length=1, max_length=4000)
    period_start: date | None = None
    period_end: date | None = None
    status: Literal["draft", "active", "completed", "cancelled"] | None = None


class WorkerPlanStep(BaseModel):
    action: str = Field(min_length=1, max_length=500)
    expected_output: str = Field(min_length=1, max_length=1000)


class WorkerPlanCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    approach: str = Field(default="", max_length=4000)
    steps: list[WorkerPlanStep] = Field(default_factory=list, max_length=30)


class WorkerPlanPatchBody(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    approach: str | None = Field(default=None, max_length=4000)
    steps: list[WorkerPlanStep] | None = Field(default=None, max_length=30)
    status: Literal["draft", "ready", "cancelled"] | None = None


class WorkerCapabilityChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list, max_length=20)
    knowledge: list[str] = Field(default_factory=list, max_length=20)
    operating_guidance: list[str] = Field(default_factory=list, max_length=20)
    skill_package_ids: list[str] = Field(default_factory=list, max_length=20)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=20)


class WorkerCapabilityProposalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_run_id: str = Field(min_length=1, max_length=128)
    source_summary: str = Field(min_length=1, max_length=4000)
    observed_gaps: str = Field(min_length=1, max_length=4000)
    proposed_changes: WorkerCapabilityChanges
    expected_benefit: str = Field(min_length=1, max_length=4000)
    risk_notes: str = Field(default="", max_length=4000)


class WorkerCapabilityReviewBody(BaseModel):
    approve: bool
    review_note: str = Field(default="", max_length=4000)


class AkbKnowledgeSourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mcp_server: str = Field(min_length=1, max_length=128)
    knowledge_base_code: str = Field(min_length=1, max_length=128)


class AkbKnowledgeSourcesPutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_capability_version: int = Field(ge=1)
    sources: list[AkbKnowledgeSourceBinding] = Field(default_factory=list, max_length=20)


class ProductKnowledgeSourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    mcp_server: str = Field(min_length=1, max_length=128)
    knowledge_base_ref: str = Field(min_length=1, max_length=256)
    selector_argument: str | None = Field(default=None, min_length=1, max_length=128)
    read_tools: list[str] = Field(min_length=1, max_length=20)
    forbidden_arguments: list[str] = Field(default_factory=list, max_length=20)


class ProductKnowledgeSourcesPutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_capability_version: int = Field(ge=1)
    sources: list[ProductKnowledgeSourceBinding] = Field(default_factory=list, max_length=20)


class PlatformMemorySourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    mcp_server: str = Field(min_length=1, max_length=128)
    scope_type: Literal["AGENT", "TEAM", "PROJECT", "TASK", "WORKFLOW", "SESSION"] = "AGENT"
    scope_code: str | None = Field(default=None, min_length=1, max_length=128)
    read_enabled: bool = True
    capture_enabled: bool = True


class PlatformMemorySourcesPutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_capability_version: int = Field(ge=1)
    sources: list[PlatformMemorySourceBinding] = Field(default_factory=list, max_length=20)


class WorkerMemoryCandidateReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    review_note: str = Field(default="", max_length=4000)
    content: str | None = Field(default=None, min_length=1, max_length=4000)


def _service(server: Any, user: Any) -> DigitalWorkerService:
    services = getattr(server, "services", None)
    if services is None:
        raise HTTPException(status_code=503, detail="database is not ready")
    return DigitalWorkerService(services.repos, user)


def _memory_service(server: Any, user: Any) -> WorkerMemoryService:
    services = getattr(server, "services", None)
    if services is None:
        raise HTTPException(status_code=503, detail="database is not ready")
    connector_service = ConnectorService(
        repo=services.repos.connector_repo,
        secret_repo=services.secret_repo,
        settings_repo=services.settings_repo,
        config=services.config,
    )
    return WorkerMemoryService(services.repos, user, connector_service)


def _call[T](callback: Callable[[], T]) -> T:
    try:
        return callback()
    except DigitalWorkerError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def _updates(body: BaseModel, nullable: set[str] | None = None) -> dict[str, Any]:
    values = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    allowed_nulls = nullable or set()
    invalid = [key for key, value in values.items() if value is None and key not in allowed_nulls]
    if invalid:
        raise HTTPException(
            status_code=422, detail="null is not allowed for: " + ", ".join(invalid)
        )
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in values.items()
    }


@router.get(
    "/organizations/{organization_id}/digital-workers",
    summary="List virtual workers in an organization",
)
async def list_workers(
    organization_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    return _call(lambda: _service(server, user).list_workers(organization_id))


@router.post(
    "/organizations/{organization_id}/digital-workers",
    status_code=201,
    summary="Create a virtual worker from an appointed Agent",
    description="Creates a governed role profile. It does not grant tools or start Agent execution.",
)
async def create_worker(
    organization_id: str,
    body: DigitalWorkerCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _call(lambda: _service(server, user).create_worker(organization_id, **body.model_dump()))


@router.patch(
    "/organizations/{organization_id}/digital-workers/{worker_id}",
    summary="Update a virtual worker profile",
)
async def update_worker(
    organization_id: str,
    worker_id: str,
    body: DigitalWorkerPatchBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    return _call(
        lambda: service.update_worker(
            organization_id,
            worker_id,
            expected_version=body.expected_version,
            updates=_updates(body),
        )
    )


@router.get(
    "/organizations/{organization_id}/digital-workers/{worker_id}/goals",
    summary="List stage goals for a virtual worker",
)
async def list_goals(
    organization_id: str,
    worker_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    return _call(lambda: _service(server, user).list_goals(organization_id, worker_id))


@router.post(
    "/organizations/{organization_id}/digital-workers/{worker_id}/goals",
    status_code=201,
    summary="Create a measurable stage goal",
)
async def create_goal(
    organization_id: str,
    worker_id: str,
    body: WorkerGoalCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _call(
        lambda: _service(server, user).create_goal(
            organization_id,
            worker_id,
            title=body.title,
            description=body.description,
            success_criteria=body.success_criteria,
            period_start=body.period_start.isoformat() if body.period_start else None,
            period_end=body.period_end.isoformat() if body.period_end else None,
        )
    )


@router.patch(
    "/organizations/{organization_id}/digital-workers/{worker_id}/goals/{goal_id}",
    summary="Update a virtual worker goal",
)
async def update_goal(
    organization_id: str,
    worker_id: str,
    goal_id: str,
    body: WorkerGoalPatchBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    return _call(
        lambda: service.update_goal(
            organization_id, worker_id, goal_id, _updates(body, {"period_start", "period_end"})
        )
    )


@router.delete(
    "/organizations/{organization_id}/digital-workers/{worker_id}/goals/{goal_id}",
    status_code=204,
    summary="Delete a draft virtual worker goal",
)
async def delete_goal(
    organization_id: str,
    worker_id: str,
    goal_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> None:
    _call(lambda: _service(server, user).delete_goal(organization_id, worker_id, goal_id))


@router.get(
    "/organizations/{organization_id}/digital-workers/{worker_id}/goals/{goal_id}/plans",
    summary="List draft work plans for a stage goal",
)
async def list_plans(
    organization_id: str,
    worker_id: str,
    goal_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    return _call(lambda: _service(server, user).list_plans(organization_id, worker_id, goal_id))


@router.post(
    "/organizations/{organization_id}/digital-workers/{worker_id}/goals/{goal_id}/plans",
    status_code=201,
    summary="Create a draft work plan for a stage goal",
    description="Plans are drafts only. Ready plans still require the governed scene approval flow before execution.",
)
async def create_plan(
    organization_id: str,
    worker_id: str,
    goal_id: str,
    body: WorkerPlanCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _call(
        lambda: _service(server, user).create_plan(
            organization_id,
            worker_id,
            goal_id,
            title=body.title,
            approach=body.approach,
            steps=[step.model_dump() for step in body.steps],
        )
    )


@router.patch(
    "/organizations/{organization_id}/digital-workers/{worker_id}/goals/{goal_id}/plans/{plan_id}",
    summary="Update a draft work plan",
)
async def update_plan(
    organization_id: str,
    worker_id: str,
    goal_id: str,
    plan_id: str,
    body: WorkerPlanPatchBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    service = _service(server, user)
    updates = _updates(body)
    if "steps" in updates and updates["steps"] is not None:
        updates["steps"] = [step.model_dump() for step in body.steps or []]
    return _call(
        lambda: service.update_plan(
            organization_id,
            worker_id,
            goal_id,
            plan_id,
            expected_version=body.expected_version,
            updates=updates,
        )
    )


@router.delete(
    "/organizations/{organization_id}/digital-workers/{worker_id}/goals/{goal_id}/plans/{plan_id}",
    status_code=204,
    summary="Delete a draft work plan",
)
async def delete_plan(
    organization_id: str,
    worker_id: str,
    goal_id: str,
    plan_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> None:
    _call(lambda: _service(server, user).delete_plan(organization_id, worker_id, goal_id, plan_id))


@router.get(
    "/organizations/{organization_id}/digital-workers/{worker_id}/capability-proposals",
    summary="List virtual worker capability improvement proposals",
)
async def list_capability_proposals(
    organization_id: str,
    worker_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    return _call(
        lambda: _service(server, user).list_capability_proposals(organization_id, worker_id)
    )


@router.get(
    "/organizations/{organization_id}/digital-workers/{worker_id}/authorized-resources",
    summary="List skill and knowledge resources already authorized for the worker Agent",
)
async def list_authorized_resources(
    organization_id: str,
    worker_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, list[dict[str, Any]]]:
    return _call(
        lambda: _service(server, user).list_authorized_resources(organization_id, worker_id)
    )


@router.put(
    "/organizations/{organization_id}/digital-workers/{worker_id}/akb-knowledge-sources",
    summary="Bind read-only AKB knowledge sources to a virtual worker",
    description=(
        "The AKB MCP connection must already be allowed by an enterprise scene for the worker's "
        "Agent and position. AKB API keys remain in the encrypted connector store."
    ),
)
async def put_akb_knowledge_sources(
    organization_id: str,
    worker_id: str,
    body: AkbKnowledgeSourcesPutBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _call(
        lambda: _service(server, user).set_akb_knowledge_sources(
            organization_id,
            worker_id,
            expected_capability_version=body.expected_capability_version,
            sources=[source.model_dump() for source in body.sources],
        )
    )


@router.put(
    "/organizations/{organization_id}/digital-workers/{worker_id}/knowledge-sources",
    summary="Bind product knowledge bases to a virtual worker",
    description=(
        "Binds read-scoped knowledge MCP tools from any product already allowed by an enterprise "
        "scene. Configure the provider, readable tool names, and optional knowledge-base selector."
    ),
)
async def put_product_knowledge_sources(
    organization_id: str,
    worker_id: str,
    body: ProductKnowledgeSourcesPutBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _call(
        lambda: _service(server, user).set_knowledge_sources(
            organization_id,
            worker_id,
            expected_capability_version=body.expected_capability_version,
            sources=[source.model_dump() for source in body.sources],
        )
    )


@router.post(
    "/organizations/{organization_id}/digital-workers/{worker_id}/knowledge-sources/diagnose",
    summary="Diagnose a worker's approved knowledge connections",
    description=(
        "Checks every bound knowledge source against the caller-visible connector, tests its "
        "upstream connection, and reports credential expiry or rotation reminders."
    ),
)
async def diagnose_product_knowledge_sources(
    organization_id: str,
    worker_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    services = getattr(server, "services", None)
    if services is None:
        raise HTTPException(status_code=503, detail="database is not ready")
    worker = _call(lambda: _service(server, user).get_worker(organization_id, worker_id))
    sources = (worker.get("capabilities") or {}).get("knowledge_sources", [])
    now = int(time.time())
    connectors = services.repos.connector_repo.list_visible(user.id)
    by_server = {row.mcp_server_name: row for row in connectors}
    connector_service = ConnectorService(
        repo=services.repos.connector_repo,
        secret_repo=services.secret_repo,
        settings_repo=services.settings_repo,
        config=services.config,
    )
    checks: list[dict[str, Any]] = []
    probe_results: dict[str, dict[str, Any]] = {}
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        server_name = str(source.get("mcp_server") or "")
        row = by_server.get(server_name)
        item: dict[str, Any] = {
            "provider": source.get("provider"),
            "mcp_server": server_name,
            "knowledge_base_ref": source.get("knowledge_base_ref"),
            "health": "unknown",
            "credential": {"state": "missing", "expires_at": None, "rotated_at": None},
        }
        if row is None:
            item.update(
                health="connector_unavailable", message="Connector is not visible to this account"
            )
            checks.append(item)
            continue
        item["connector_name"] = row.display_name
        item["credential"] = credential_rotation_state(
            has_credentials=row.has_credentials,
            expires_at=row.credential_expires_at,
            rotated_at=row.credential_rotated_at,
            now=now,
        )
        if row.status != "active":
            item.update(health="disabled", message="Connector is disabled")
        elif not row.has_credentials:
            item.update(health="credentials_missing", message="Connector credentials are missing")
        elif item["credential"]["state"] == "expired":
            item.update(health="credentials_expired", message="Connector credentials have expired")
        elif row.instance_id not in probe_results:
            entry = get_catalog_entry(row.kind)
            if entry is None:
                result = {"ok": False}
            else:
                credentials = connector_service.decrypt(row.instance_id)
                try:
                    result = await probe_connector(
                        entry,
                        credentials,
                        instance_id=row.instance_id,
                        config=services.config,
                    )
                except Exception:
                    result = {"ok": False}
            probe_results[row.instance_id] = result
            item.update(
                health="healthy" if result.get("ok") else "connection_failed",
                message=(
                    "Connection check passed"
                    if result.get("ok")
                    else "Connection check failed; review the service URL, credentials, and upstream availability"
                ),
            )
        else:
            result = probe_results[row.instance_id]
            item.update(
                health="healthy" if result.get("ok") else "connection_failed",
                message="Connection check passed"
                if result.get("ok")
                else "Connection check failed",
            )
        checks.append(item)
    return {
        "worker_id": worker_id,
        "checked_at": now,
        "summary": {
            "total": len(checks),
            "healthy": sum(item["health"] == "healthy" for item in checks),
            "attention_required": sum(
                item["health"] != "healthy"
                or item["credential"]["state"] in {"expires_soon", "rotation_due"}
                for item in checks
            ),
        },
        "sources": checks,
    }


@router.put(
    "/organizations/{organization_id}/digital-workers/{worker_id}/memory-sources",
    summary="Bind read-only platform memory to a virtual worker",
    description=(
        "Binds only memory_recall from a memory MCP connection already authorized by a scene. "
        "Octop fixes the memory scope for each run; write and forget tools are rejected."
    ),
)
async def put_platform_memory_sources(
    organization_id: str,
    worker_id: str,
    body: PlatformMemorySourcesPutBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _call(
        lambda: _service(server, user).set_memory_sources(
            organization_id,
            worker_id,
            expected_capability_version=body.expected_capability_version,
            sources=[source.model_dump() for source in body.sources],
        )
    )


@router.get(
    "/organizations/{organization_id}/memory-candidates",
    summary="List platform-memory candidates pending or completed review",
)
async def list_worker_memory_candidates(
    organization_id: str,
    worker_id: str | None = None,
    status: Literal["pending", "writing", "written", "rejected", "failed"] | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    return _call(
        lambda: _memory_service(server, user).list_candidates(
            organization_id, worker_id=worker_id, status=status, limit=limit
        )
    )


@router.post(
    "/organizations/{organization_id}/memory-candidates/{candidate_id}/review",
    summary="Approve or reject a platform-memory candidate",
    description=(
        "Approval writes the reviewed content to the already-bound AKB memory scope. "
        "The request uses the candidate owner's encrypted AKB connector and a retry-safe idempotency key."
    ),
)
async def review_worker_memory_candidate(
    organization_id: str,
    candidate_id: str,
    body: WorkerMemoryCandidateReviewBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return await _memory_service(server, user).review_candidate(
            organization_id,
            candidate_id,
            approve=body.approve,
            review_note=body.review_note,
            content=body.content,
        )
    except DigitalWorkerError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.post(
    "/organizations/{organization_id}/digital-workers/{worker_id}/capability-proposals",
    status_code=201,
    summary="Propose a capability improvement from successful execution evidence",
    description=(
        "Submits descriptive skill, knowledge, or operating-guidance changes for independent review. "
        "This never grants tools, connectors, or permissions."
    ),
)
async def create_capability_proposal(
    organization_id: str,
    worker_id: str,
    body: WorkerCapabilityProposalBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _call(
        lambda: _service(server, user).create_capability_proposal(
            organization_id,
            worker_id,
            **body.model_dump(),
        )
    )


@router.post(
    "/organizations/{organization_id}/digital-workers/{worker_id}/capability-proposals/{proposal_id}/review",
    summary="Review and publish or reject a capability proposal",
    description=(
        "Publishing appends the reviewed descriptive capability changes and increments the capability "
        "version. The reviewer must differ from the proposal author."
    ),
)
async def review_capability_proposal(
    organization_id: str,
    worker_id: str,
    proposal_id: str,
    body: WorkerCapabilityReviewBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _call(
        lambda: _service(server, user).review_capability_proposal(
            organization_id, worker_id, proposal_id, **body.model_dump()
        )
    )
