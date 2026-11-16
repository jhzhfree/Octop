"""Read-only, bounded persisted execution and human-review counts."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from octop.api.deps import current_user, get_server
from octop.infra.db.repos.organization_progress import OrganizationProgressRepo, ProgressAccessError
from octop.infra.scenes.progress import OrganizationProgressService

router = APIRouter()


class ExecutionCounts(BaseModel):
    awaiting_approval: int
    rejected: int
    queued: int
    succeeded: int
    failed: int


class DeliveryCounts(BaseModel):
    unreviewed_success: int
    accepted: int
    changes_requested: int


class ProgressCounts(BaseModel):
    execution: ExecutionCounts
    delivery: DeliveryCounts


class ProgressSummary(BaseModel):
    organization_id: str
    window_start: int
    window_end_exclusive: int
    as_of: int
    scope: Literal["persisted_runs_by_creation_time"]
    unit: Literal["run"]
    max_runs: int
    limit_exceeded: bool
    denominator: int | None
    counts: ProgressCounts | None
    inconsistent_records: int | None = None
    persisted_state_may_lag: Literal[True] = True
    external_systems_verified: Literal[False] = False
    execution_authorized: Literal[False] = False


@router.get(
    "/organizations/{organization_id}/progress",
    response_model=ProgressSummary,
    summary="Count persisted organization runs in a bounded creation-time window",
)
def organization_progress(
    organization_id: str,
    response: Response,
    start: int = Query(ge=0, le=253402300799),
    end: int = Query(gt=0, le=253402300799),
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if getattr(server, "services", None) is None:
        raise HTTPException(
            status_code=503, detail="database is not ready", headers={"Cache-Control": "no-store"}
        )
    service = OrganizationProgressService(OrganizationProgressRepo(server.services.db), user.id)
    try:
        result = service.inspect(organization_id, start=start, end=end)
    except ProgressAccessError as exc:
        raise HTTPException(
            status_code=403,
            detail="organization manager access required",
            headers={"Cache-Control": "no-store"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=str(exc), headers={"Cache-Control": "no-store"}
        ) from exc
    result["as_of"] = result.pop("observed_at")
    return result


class QueueItem(BaseModel):
    run_id: str
    status: Literal["awaiting_approval", "queued", "rejected", "failed", "succeeded"]
    created_at: int
    decision: Literal["accepted", "changes_requested"] | None


class RunQueue(BaseModel):
    worker_id: str | None = None
    goal_id: str | None = None
    organization_id: str
    category: Literal[
        "awaiting_approval",
        "queued",
        "rejected",
        "failed",
        "unreviewed_success",
        "changes_requested",
        "accepted",
    ]
    as_of: int
    items: list[QueueItem]
    next_before: int | None
    execution_authorized: Literal[False] = False
    persisted_state_may_lag: Literal[True] = True


@router.get(
    "/organizations/{organization_id}/run-queue",
    response_model=RunQueue,
    summary="List organization run follow-ups with bounded keyset pagination",
    description="Managers only. Optional worker_id and goal_id filters by current persisted plan and goal ownership within this organization; unlinked runs remain in the unfiltered view. Newest inserted runs first. Includes queued and rejected requests, plus accepted deliveries for follow-up; queued is a persisted state, not proof of active execution; acceptance does not mean publication or goal completion. Read-only; no dispatch, retry, or acceptance.",
)
def organization_run_queue(
    organization_id: str,
    response: Response,
    category: Literal[
        "awaiting_approval",
        "queued",
        "rejected",
        "failed",
        "unreviewed_success",
        "changes_requested",
        "accepted",
    ],
    limit: int = Query(default=20, ge=1, le=50),
    before: int | None = Query(default=None, ge=1, le=2**63 - 1),
    worker_id: str | None = Query(default=None, min_length=1, max_length=128),
    goal_id: str | None = Query(default=None, min_length=1, max_length=128),
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if getattr(server, "services", None) is None:
        raise HTTPException(
            status_code=503, detail="database is not ready", headers={"Cache-Control": "no-store"}
        )
    service = OrganizationProgressService(OrganizationProgressRepo(server.services.db), user.id)
    try:
        return service.queue(
            organization_id,
            category=category,
            limit=limit,
            before=before,
            worker_id=worker_id,
            goal_id=goal_id,
        )
    except ProgressAccessError as exc:
        raise HTTPException(
            status_code=403,
            detail="organization manager access required",
            headers={"Cache-Control": "no-store"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=str(exc), headers={"Cache-Control": "no-store"}
        ) from exc


class RunQueueDetail(BaseModel):
    run_id: str
    organization_id: str
    scene_id: str
    scene_name: str
    scene_version: int
    plan_id: str | None
    status: Literal["awaiting_approval", "rejected", "queued", "succeeded", "failed"]
    created_at: int
    updated_at: int
    decision: Literal["accepted", "changes_requested"] | None
    as_of: int
    persisted_state_may_lag: Literal[True] = True


@router.get(
    "/organizations/{organization_id}/run-queue/{run_id}",
    response_model=RunQueueDetail,
    summary="Read minimal persisted run metadata for organization managers",
    description="No prompts, errors, identities, evidence or snapshots. Does not reconcile or dispatch runs.",
)
def organization_run_detail(
    organization_id: str,
    run_id: str,
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    headers = {"Cache-Control": "no-store"}
    if getattr(server, "services", None) is None:
        raise HTTPException(status_code=503, detail="database is not ready", headers=headers)
    service = OrganizationProgressService(OrganizationProgressRepo(server.services.db), user.id)
    try:
        result = service.detail(organization_id, run_id)
    except ProgressAccessError as exc:
        raise HTTPException(
            status_code=403, detail="organization manager access required", headers=headers
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="run not found", headers=headers)
    return result
