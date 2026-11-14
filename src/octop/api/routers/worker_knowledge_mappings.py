"""Local draft mapping management. Never contacts AKB or grants retrieval access."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server
from octop.i18n import tr
from octop.infra.digital_workers.knowledge_mappings import WorkerKnowledgeMappingService
from octop.infra.digital_workers.service import DigitalWorkerError
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter(
    prefix="/organizations/{organization_id}/digital-workers/{worker_id}/knowledge-mappings"
)


class MappingCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mcp_server: str = Field(min_length=1, max_length=128)
    knowledge_base_ref: str = Field(min_length=1, max_length=256)
    external_source_id: str = Field(min_length=1, max_length=128)
    expected_capability_version: int = Field(ge=1, strict=True)
    expected_connection_revision: int = Field(ge=1, strict=True)
    expected_resource_revision: int = Field(ge=1, strict=True)


class MappingRevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1, strict=True)


class MappingResponse(BaseModel):
    mapping_id: str
    organization_id: str
    worker_id: str
    capability_key: str
    capability_json: str = Field(description="Local capability snapshot, not an AKB grant.")
    capability_version: int
    worker_version: int
    external_source_id: str
    expected_connection_id: str
    connection_revision: int
    resource_revision: int
    revision: int
    status: Literal["draft", "revoked"]
    created_by: int
    updated_by: int
    created_at: int
    updated_at: int
    local_snapshot_current: bool | None = Field(
        description="Null on lists (not checked); boolean on individual inspection. Local observation only, not remote ACL verification."
    )
    retrieval_enabled: Literal[False]
    authorization_status: Literal["delegated_user_acl_unverified"]


class MappingPage(BaseModel):
    items: list[MappingResponse]
    limit: int
    offset: int


def _service(server: Any, user: Any, request: Request) -> WorkerKnowledgeMappingService:
    services = getattr(server, "services", None)
    if services is None:
        raise HTTPException(
            503, detail=tr("knowledgeMappingApi.unavailable", resolve_request_locale(request))
        )
    return WorkerKnowledgeMappingService(services.repos, user)


def _call[T](request: Request, callback: Callable[[], T]) -> T:
    try:
        return callback()
    except DigitalWorkerError as exc:
        key = {403: "forbidden", 404: "notFound", 409: "conflict"}.get(exc.status, "unavailable")
        raise HTTPException(
            exc.status,
            detail={
                "code": exc.code,
                "message": tr(f"knowledgeMappingApi.{key}", resolve_request_locale(request)),
            },
        ) from exc


@router.get(
    "",
    response_model=MappingPage,
    summary="List local knowledge mapping drafts",
    description="Organization managers only. Bounded offset pagination, newest first. No remote calls or retrieval authorization.",
)
def list_mappings(
    organization_id: str,
    worker_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> MappingPage:
    rows = _call(
        request,
        lambda: _service(server, user, request).list_mappings(
            organization_id, worker_id, limit=limit, offset=offset
        ),
    )
    return MappingPage(
        items=[MappingResponse.model_validate(row) for row in rows], limit=limit, offset=offset
    )


@router.post(
    "",
    status_code=201,
    response_model=MappingResponse,
    summary="Create an explicit local mapping draft",
    description="Requires organization management, existing authorized bindings and exact expected versions. Never activates retrieval, signs an AKB request or changes capabilities.",
)
def create_mapping(
    organization_id: str,
    worker_id: str,
    body: MappingCreateBody,
    request: Request,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> MappingResponse:
    row = _call(
        request,
        lambda: _service(server, user, request).create_draft(
            organization_id, worker_id, **body.model_dump()
        ),
    )
    return MappingResponse.model_validate(row)


@router.get(
    "/{mapping_id}",
    response_model=MappingResponse,
    summary="Inspect a local mapping snapshot",
    description="Managers only. Rechecks local bindings; a current snapshot does not establish current AKB user ACL.",
)
def inspect_mapping(
    organization_id: str,
    worker_id: str,
    mapping_id: str,
    request: Request,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> MappingResponse:
    row = _call(
        request,
        lambda: _service(server, user, request).inspect(organization_id, worker_id, mapping_id),
    )
    return MappingResponse.model_validate(row)


@router.post(
    "/{mapping_id}/revoke",
    response_model=MappingResponse,
    summary="Revoke a local mapping draft",
    description="Requires the current revision. Revoked records cannot activate retrieval. Re-drafting requires a new explicit create request and revalidation.",
)
def revoke_mapping(
    organization_id: str,
    worker_id: str,
    mapping_id: str,
    body: MappingRevokeBody,
    request: Request,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> MappingResponse:
    row = _call(
        request,
        lambda: _service(server, user, request).revoke(
            organization_id, worker_id, mapping_id, expected_revision=body.expected_revision
        ),
    )
    return MappingResponse.model_validate(row)
