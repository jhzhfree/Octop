"""External knowledge source HTTP API.

Thin HTTP layer over :class:`ExternalKnowledgeService`: validation and error
mapping only, all domain rules live in ``infra/knowledge/external``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.external.service import ExternalKnowledgeService
from octop.infra.knowledge.external.types import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    ExternalSourceError,
)

router = APIRouter()

_TARGET_TYPES = Literal["agent", "knowledge_base"]

_ERROR_CODES: dict[str, ErrorCode] = {
    "NOT_FOUND": ErrorCode.KNOWLEDGE_NOT_FOUND,
    "FORBIDDEN": ErrorCode.KNOWLEDGE_FORBIDDEN,
    "INVALID_CREDENTIALS": ErrorCode.CONNECTOR_INVALID_CREDENTIALS,
}


def _service(server: Any) -> ExternalKnowledgeService:
    services = server.services
    return ExternalKnowledgeService(
        services.external_knowledge_repo,
        services.connector_repo,
        services.secret_repo,
        services.config,
    )


def _fail(exc: ExternalSourceError) -> OctopError:
    return OctopError(_ERROR_CODES.get(exc.code, ErrorCode.KNOWLEDGE_UNSUPPORTED_TYPE), str(exc))


def _run[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ExternalSourceError as exc:
        raise _fail(exc) from exc


class SourceCreateBody(BaseModel):
    kind: str = Field(description="Adapter kind, e.g. akb-mcp")
    display_name: str = Field(min_length=1, max_length=120)
    endpoint: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(default="", max_length=2048)
    instance_id: str = Field(default="", max_length=120)
    ref: str = Field(default="", max_length=255)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    timeout_ms: int = Field(default=10_000, ge=1, le=60_000)
    allow_insecure_http: bool = Field(default=False)
    selector_argument: str = Field(default="", max_length=120)
    forbidden_arguments: list[str] = Field(default_factory=list)
    read_tools: list[str] = Field(default_factory=list)
    shared: bool = Field(default=False)


class SourcePatchBody(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=2048)
    ref: str | None = Field(default=None, max_length=255)
    top_k: int | None = Field(default=None, ge=1, le=MAX_TOP_K)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    timeout_ms: int | None = Field(default=None, ge=1, le=60_000)
    allow_insecure_http: bool | None = None
    selector_argument: str | None = Field(default=None, max_length=120)
    forbidden_arguments: list[str] | None = None
    read_tools: list[str] | None = None
    enabled: bool | None = None
    shared: bool | None = None


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=0, ge=0, le=MAX_TOP_K)


class BindingBody(BaseModel):
    target_type: _TARGET_TYPES
    target_id: str = Field(min_length=1, max_length=255)
    enabled: bool = True


class RetrieveBody(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    target_type: _TARGET_TYPES
    target_ids: list[str] = Field(default_factory=list, max_length=50)
    limit: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)


@router.get("/external-knowledge/kinds", summary="Supported external knowledge kinds")
async def list_kinds(
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """List adapter kinds an external knowledge source can use."""
    return {"kinds": _service(server).supported_kinds()}


@router.get("/external-knowledge/sources", summary="List external knowledge sources")
async def list_sources(
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    """List sources visible to the caller, without credentials."""
    return _run(
        lambda: _service(server).list_sources(user_id=user.id, is_admin=user.role == "admin")
    )


@router.post("/external-knowledge/sources", status_code=201, summary="Create source")
async def create_source(
    body: SourceCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Register one external knowledge source and encrypt its credentials."""
    svc = _service(server)
    return _run(
        lambda: svc.create_source(
            user_id=user.id,
            kind=body.kind,
            display_name=body.display_name,
            endpoint=body.endpoint,
            api_key=body.api_key,
            instance_id=body.instance_id,
            ref=body.ref,
            top_k=body.top_k,
            min_score=body.min_score,
            timeout_ms=body.timeout_ms,
            allow_insecure_http=body.allow_insecure_http,
            selector_argument=body.selector_argument,
            forbidden_arguments=body.forbidden_arguments,
            read_tools=body.read_tools,
            shared=body.shared,
        )
    )


@router.patch("/external-knowledge/sources/{source_id}", summary="Update source")
async def update_source(
    source_id: str,
    body: SourcePatchBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Patch configuration, credentials or the enabled flag."""
    svc = _service(server)
    fields = body.model_dump(exclude_none=True)
    return _run(
        lambda: svc.update_source(
            source_id,
            user_id=user.id,
            is_admin=user.role == "admin",
            **fields,
        )
    )


@router.delete("/external-knowledge/sources/{source_id}", status_code=204, summary="Delete source")
async def delete_source(
    source_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> None:
    """Delete a source and cascade its bindings."""
    _run(
        lambda: _service(server).delete_source(
            source_id, user_id=user.id, is_admin=user.role == "admin"
        )
    )


@router.post("/external-knowledge/sources/{source_id}/probe", summary="Probe source connectivity")
async def probe_source(
    source_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Check reachability and list the tools the credential can use."""
    return await _service(server).probe(source_id, user_id=user.id, is_admin=user.role == "admin")


@router.get("/external-knowledge/sources/{source_id}/refs", summary="List knowledge bases")
async def list_source_refs(
    source_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """List the knowledge bases the stored credential is allowed to read."""
    return {
        "refs": await _service(server).list_refs(
            source_id, user_id=user.id, is_admin=user.role == "admin"
        )
    }


@router.post("/external-knowledge/sources/{source_id}/search", summary="Search one source")
async def search_source(
    source_id: str,
    body: SearchBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Run a retrieval test against one source and return normalized chunks."""
    try:
        return await _service(server).search_source(
            source_id,
            body.query,
            user_id=user.id,
            is_admin=user.role == "admin",
            top_k=body.top_k,
        )
    except ExternalSourceError as exc:
        raise _fail(exc) from exc


@router.get("/external-knowledge/sources/{source_id}/bindings", summary="List source bindings")
async def list_bindings(
    source_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    """List the agents and local knowledge bases bound to this source."""
    return _service(server).list_bindings(source_id)


@router.post("/external-knowledge/sources/{source_id}/bindings", summary="Bind source to target")
async def create_binding(
    source_id: str,
    body: BindingBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Bind a source to an agent or a local knowledge base."""
    svc = _service(server)
    return _run(
        lambda: svc.set_binding(
            source_id,
            user_id=user.id,
            is_admin=user.role == "admin",
            target_type=body.target_type,
            target_id=body.target_id,
            enabled=body.enabled,
        )
    )


@router.delete(
    "/external-knowledge/sources/{source_id}/bindings/{target_type}/{target_id}",
    summary="Unbind source from target",
)
async def delete_binding(
    source_id: str,
    target_type: str,
    target_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> None:
    """Remove one binding."""
    _run(
        lambda: _service(server).delete_binding(
            source_id,
            user_id=user.id,
            is_admin=user.role == "admin",
            target_type=target_type,
            target_id=target_id,
        )
    )


@router.post("/external-knowledge/retrieve", summary="Retrieve across bound sources")
async def retrieve(
    body: RetrieveBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Fuse results from every source bound to the given targets."""
    svc = _service(server)
    specs = svc.specs_for_targets(body.target_type, list(body.target_ids))
    visible = {
        item["source_id"]
        for item in svc.list_sources(user_id=user.id, is_admin=user.role == "admin")
    }
    specs = [spec for spec in specs if spec.binding_id in visible]
    return await svc.retrieve(query=body.query, specs=specs, limit=body.limit)
