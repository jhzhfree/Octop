"""Virtual worker profiles and stage goals."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server
from octop.infra.digital_workers import DigitalWorkerError, DigitalWorkerService

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


def _service(server: Any, user: Any) -> DigitalWorkerService:
    services = getattr(server, "services", None)
    if services is None:
        raise HTTPException(status_code=503, detail="database is not ready")
    return DigitalWorkerService(services.repos, user)


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
) -> dict[str, list[dict[str, str]]]:
    return _call(
        lambda: _service(server, user).list_authorized_resources(organization_id, worker_id)
    )


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
