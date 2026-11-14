"""Read-only observations, never execution authorization."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from octop.api.deps import current_user, get_server
from octop.infra.digital_workers.readiness import PlanReadinessReport, WorkerPlanReadinessService
from octop.infra.digital_workers.service import DigitalWorkerError

router = APIRouter()


@router.get(
    "/organizations/{organization_id}/digital-workers/{worker_id}/goals/{goal_id}/plans/{plan_id}/readiness",
    response_model=PlanReadinessReport,
    summary="Observe local plan prerequisites without authorizing execution",
)
def inspect_plan_readiness(
    organization_id: str,
    worker_id: str,
    goal_id: str,
    plan_id: str,
    response: Response,
    expected_worker_version: int = Query(ge=1),
    expected_plan_version: int = Query(ge=1),
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> PlanReadinessReport:
    headers = {"Cache-Control": "no-store"}
    response.headers.update(headers)
    services = getattr(server, "services", None)
    if services is None:
        raise HTTPException(503, detail={"code": "READINESS_UNAVAILABLE"}, headers=headers)
    try:
        return WorkerPlanReadinessService(services.repos, user).inspect(
            organization_id,
            worker_id,
            goal_id,
            plan_id,
            expected_worker_version=expected_worker_version,
            expected_plan_version=expected_plan_version,
        )
    except DigitalWorkerError as exc:
        # Stable codes only: clients localize messages and never expose raw internals.
        raise HTTPException(exc.status, detail={"code": exc.code}, headers=headers) from exc
