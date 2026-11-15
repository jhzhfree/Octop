"""Local publication preview and independent approval; never sends to Mattermost."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server
from octop.i18n import tr
from octop.infra.scenes.mattermost_publication import MattermostPublicationService
from octop.infra.scenes.service import SceneError
from octop.infra.utils.locale import resolve_request_locale


class _PublicationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                raise HTTPException(
                    status_code=422,
                    detail=tr("mattermost.readiness_invalid", resolve_request_locale(request)),
                    headers={"Cache-Control": "no-store"},
                ) from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return handler


router = APIRouter(route_class=_PublicationRoute)
_PATH = "/organizations/{organization_id}/scene-runs/{run_id}/mattermost-publication"


class PublicationPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_worker_version: int = Field(gt=0)
    expected_intent_revision: int = Field(gt=0)
    expected_configuration_revision: int = Field(gt=0)


class PublicationCandidate(PublicationPreviewBody):
    model_config = ConfigDict(extra="ignore", strict=True)


class PublicationDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_publication_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublicationRecord(BaseModel):
    organization_id: str
    run_id: str
    worker_id: str
    worker_version: int
    intent_revision: int
    configuration_revision: int
    instance_key: str
    team_id: str
    channel_id: str
    bot_id: str
    report_sha256: str
    publication_sha256: str
    content: str
    proposed_by: int
    approved_by: int | None
    status: Literal["pending_approval", "blocked_not_connected", "revoked", "unknown"]
    created_at: int
    updated_at: int
    connected: Literal[False]
    dispatch_enabled: Literal[False]
    execution_authorized: Literal[False]


def _service(server: Any, user: Any) -> MattermostPublicationService:
    if server.services is None:
        raise HTTPException(status_code=503, detail="database is not ready")
    return MattermostPublicationService(server.services.repos, user.id)


def _http_error(exc: SceneError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    _PATH + "/candidate",
    response_model=PublicationCandidate,
    summary="Read current publication preview preconditions without creating a preview",
    description="Requester only. Rechecks current permissions, accepted report and destination "
    "revisions. Returns only four optimistic preconditions; no content, destination, credentials, "
    "publication mutation, audit write or network operation. POST revalidates these values.",
)
def get_publication_candidate(
    organization_id: str,
    run_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return _service(server, user).candidate(organization_id, run_id)
    except SceneError as exc:
        raise _http_error(exc) from exc


@router.get(
    _PATH,
    response_model=PublicationRecord,
    summary="Read the local immutable Mattermost publication preview",
)
def get_publication(
    organization_id: str,
    run_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return _service(server, user).get(organization_id, run_id)
    except SceneError as exc:
        raise _http_error(exc) from exc


@router.post(
    _PATH,
    response_model=PublicationRecord,
    summary="Create a local publication preview from an accepted inspection report",
    description="Requester only. Content and destination come from stored snapshots, not the "
    "request body. This creates a local preview only and never sends to Mattermost.",
)
def preview_publication(
    organization_id: str,
    run_id: str,
    body: PublicationPreviewBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return _service(server, user).preview(organization_id, run_id, **body.model_dump())
    except SceneError as exc:
        raise _http_error(exc) from exc


@router.post(
    _PATH + "/approve",
    response_model=PublicationRecord,
    summary="Independently approve a preview into a blocked local outbox",
    description="Separate from report acceptance. Self-approval is forbidden. Approval leaves "
    "the record blocked_not_connected: no dispatch or network request is performed.",
)
def approve_publication(
    organization_id: str,
    run_id: str,
    body: PublicationDecisionBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return _service(server, user).approve(organization_id, run_id, **body.model_dump())
    except SceneError as exc:
        raise _http_error(exc) from exc


@router.post(
    _PATH + "/revoke",
    response_model=PublicationRecord,
    summary="Revoke a local publication without sending anything",
)
def revoke_publication(
    organization_id: str,
    run_id: str,
    body: PublicationDecisionBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    try:
        return _service(server, user).revoke(organization_id, run_id, **body.model_dump())
    except SceneError as exc:
        raise _http_error(exc) from exc
