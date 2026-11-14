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
    status: Literal["awaiting_approval", "failed", "succeeded"]
    created_at: int
    decision: Literal["changes_requested"] | None


class RunQueue(BaseModel):
    organization_id: str
    category: Literal["awaiting_approval", "failed", "unreviewed_success", "changes_requested"]
    as_of: int
    items: list[QueueItem]
    next_before: int | None
    execution_authorized: Literal[False] = False
    persisted_state_may_lag: Literal[True] = True


@router.get(
    "/organizations/{organization_id}/run-queue",
    response_model=RunQueue,
    summary="List organization run follow-ups with bounded keyset pagination",
    description="Managers only. Newest inserted runs first. Read-only; no dispatch, retry, or acceptance.",
)
def organization_run_queue(
    organization_id: str,
    response: Response,
    category: Literal["awaiting_approval", "failed", "unreviewed_success", "changes_requested"],
    limit: int = Query(default=20, ge=1, le=50),
    before: int | None = Query(default=None, ge=1, le=2**63 - 1),
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
        return service.queue(organization_id, category=category, limit=limit, before=before)
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
