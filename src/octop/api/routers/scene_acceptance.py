"""Explicit human delivery-summary review, separate from execution approval."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server
from octop.infra.db.repos.scene_acceptance import SceneAcceptanceRepo
from octop.infra.scenes.acceptance import SceneAcceptanceService
from octop.infra.scenes.evidence import scene_run_evidence
from octop.infra.scenes.report_attachment import SceneReportAttachmentService
from octop.infra.scenes.service import EnterpriseSceneService, SceneError

router = APIRouter()


class AcceptanceDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "changes_requested"]
    expected_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AcceptanceRecord(BaseModel):
    run_id: str
    organization_id: str
    decision: Literal["accepted", "changes_requested"]
    reviewer_id: int
    reviewed_at: int
    evidence_sha256: str
    evidence_length: int
    snapshot_sha256: str
    scene_version: int


class AcceptanceReviewRecord(BaseModel):
    run_id: str
    answer: str
    evidence_sha256: str
    evidence_length: int
    snapshot_sha256: str
    acceptance: AcceptanceRecord | None


def _service(server: Any, user: Any) -> SceneAcceptanceService:
    if server.services is None:
        raise HTTPException(status_code=503, detail="database is not ready")

    async def load_evidence(run: dict[str, Any]) -> str | None:
        runtime = server.app_runtime
        return await scene_run_evidence(
            run=run,
            cron_manager=runtime.cron_manager,
            thread_registry=runtime.gateway.thread_registry,
            agent_registry=runtime.agent_registry,
            thread_message_repo=server.services.thread_message_repo,
            history_archive=getattr(runtime, "history_archive", None),
        )

    return SceneAcceptanceService(
        EnterpriseSceneService(server.services.repos, user),
        SceneAcceptanceRepo(server.services.db),
        load_evidence,
    )


def _http_error(exc: SceneError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


@router.get(
    "/organizations/{organization_id}/scene-runs/{run_id}/acceptance/review",
    response_model=AcceptanceReviewRecord,
    summary="Review a delivery summary before independent human acceptance",
)
async def review_delivery(
    organization_id: str,
    run_id: str,
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(server, user).review(organization_id, run_id)
    except SceneError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/organizations/{organization_id}/scene-runs/{run_id}/acceptance",
    response_model=AcceptanceRecord,
    summary="Record an immutable independent human delivery review",
)
async def decide_delivery(
    organization_id: str,
    run_id: str,
    body: AcceptanceDecisionBody,
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(server, user).decide(organization_id, run_id, **body.model_dump())
    except SceneError as exc:
        raise _http_error(exc) from exc


class ReportAttachmentPreview(BaseModel):
    report_run_id: str
    report_version: int
    report_content_hash: str
    content: str


class ReportAttachmentRecord(ReportAttachmentPreview):
    run_id: str
    organization_id: str
    attachment_sha256: str
    attached_by: int
    attached_at: int


class ReportAttachmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_run_id: str = Field(min_length=1, max_length=128)
    expected_report_version: int = Field(gt=0)
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    share_with_reviewer: Literal[True]


def _attachments(server: Any, user: Any) -> SceneReportAttachmentService:
    if server.services is None:
        raise HTTPException(status_code=503, detail="database is not ready")
    return SceneReportAttachmentService(EnterpriseSceneService(server.services.repos, user))


@router.get(
    "/organizations/{organization_id}/scene-runs/{run_id}/report-attachment/preview",
    response_model=ReportAttachmentPreview,
    summary="Preview an owned inspection report before explicitly sharing a snapshot",
)
def preview_report_attachment(
    organization_id: str,
    run_id: str,
    report_run_id: str,
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return _attachments(server, user).preview(organization_id, run_id, report_run_id)
    except SceneError as exc:
        raise _http_error(exc) from exc


@router.get(
    "/organizations/{organization_id}/scene-runs/{run_id}/report-attachment",
    response_model=ReportAttachmentRecord | None,
    summary="Read the immutable report shared with this run's authorized reviewer",
)
def get_report_attachment(
    organization_id: str,
    run_id: str,
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any] | None:
    response.headers["Cache-Control"] = "no-store"
    try:
        return _attachments(server, user).get(organization_id, run_id)
    except SceneError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/organizations/{organization_id}/scene-runs/{run_id}/report-attachment",
    response_model=ReportAttachmentRecord,
    summary="Share a single immutable inspection report snapshot for independent review",
    description="Requester only. Explicitly shares content, never approves outbound delivery. "
    "Forbidden after review or a prior attachment. A retry must read the existing attachment.",
)
def attach_report(
    organization_id: str,
    run_id: str,
    body: ReportAttachmentBody,
    response: Response,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return _attachments(server, user).attach(organization_id, run_id, **body.model_dump())
    except SceneError as exc:
        raise _http_error(exc) from exc
