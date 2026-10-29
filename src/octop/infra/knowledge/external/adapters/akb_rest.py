"""AKB adapter over the versioned REST retrieval endpoint.

Uses the standard retrieval contract (``POST /api/v1/external/kb/retrievals``)
with AKB's ``X-API-Key`` header, for deployments where MCP is disabled.
Knowledge bases are discovered through ``GET /api/v1/external/bases`` so the
binding can pick a base instead of guessing its code.
"""

from __future__ import annotations

from typing import Any

from octop.infra.knowledge.external.http import get_json, post_json, validate_endpoint
from octop.infra.knowledge.external.identity import IDENTITY_HEADER
from octop.infra.knowledge.external.normalize import (
    extract_items,
    filter_chunks,
    normalize_chunks,
)
from octop.infra.knowledge.external.types import (
    MAX_TOP_K,
    ExternalSearchResult,
    ExternalSourceError,
    ExternalSourceSpec,
    SourceProbeResult,
)

ORIGIN = "akb-rest"

# AKB answers rejections with both an HTTP status and a structured body code;
# the body wins so "no scope" and "unknown base" stay distinguishable.
_CODE_MAP: dict[str, str] = {
    "INVALID_ARGUMENT": "INVALID_ARGUMENT",
    "UNAUTHORIZED": "INVALID_CREDENTIALS",
    "FORBIDDEN": "FORBIDDEN",
    "KB_NOT_FOUND": "NOT_FOUND",
    "RESOURCE_EXHAUSTED": "RATE_LIMITED",
    "INTERNAL": "UPSTREAM_ERROR",
}
_STATUS_MAP: dict[int, str] = {
    401: "INVALID_CREDENTIALS",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    429: "RATE_LIMITED",
}


def _base_url(spec: ExternalSourceSpec) -> str:
    return validate_endpoint(spec.endpoint, allow_insecure_http=spec.allow_insecure_http)


def _url(spec: ExternalSourceSpec) -> str:
    return _base_url(spec) + "/api/v1/external/kb/retrievals"


def _api_key(creds: dict[str, Any]) -> str:
    api_key = str(creds.get("api_key") or "").strip()
    if not api_key:
        raise ExternalSourceError("INVALID_ARGUMENT", "AKB API key is missing", origin=ORIGIN)
    return api_key


def _headers(spec: ExternalSourceSpec, creds: dict[str, Any]) -> dict[str, str]:
    headers = {
        "X-API-Key": _api_key(creds),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if spec.identity_token:
        headers[IDENTITY_HEADER] = spec.identity_token
    return headers


def _ref_headers(spec: ExternalSourceSpec, creds: dict[str, Any]) -> dict[str, str]:
    if not spec.ref:
        raise ExternalSourceError(
            "INVALID_ARGUMENT", "an AKB knowledge base reference is required", origin=ORIGIN
        )
    return {**_headers(spec, creds), "X-Knowledge-Base-Id": spec.ref}


async def search(
    spec: ExternalSourceSpec,
    creds: dict[str, Any],
    query: str,
    *,
    top_k: int,
) -> ExternalSearchResult:
    capped = max(1, min(top_k, MAX_TOP_K))
    payload = await post_json(
        _url(spec),
        headers=_ref_headers(spec, creds),
        payload={"question": query, "topK": capped},
        timeout_seconds=spec.timeout_seconds,
        origin=ORIGIN,
        status_map=_STATUS_MAP,
        code_map=_CODE_MAP,
    )
    items = extract_items(payload, keys=("chunks",))
    chunks = normalize_chunks(items, origin=ORIGIN, ref=spec.ref)
    return ExternalSearchResult(
        chunks=filter_chunks(chunks, min_score=spec.min_score, limit=capped)
    )


async def list_refs(spec: ExternalSourceSpec, creds: dict[str, Any]) -> list[dict[str, str]]:
    """List the knowledge bases this API key may read (``GET /external/bases``)."""
    payload = await get_json(
        _base_url(spec) + "/api/v1/external/bases",
        headers=_headers(spec, creds),
        timeout_seconds=spec.timeout_seconds,
        origin=ORIGIN,
        status_map=_STATUS_MAP,
        code_map=_CODE_MAP,
    )
    refs: list[dict[str, str]] = []
    for item in extract_items(payload, keys=("bases",)):
        if not isinstance(item, dict):
            continue
        code = item.get("baseCode") or item.get("code") or item.get("id")
        if not isinstance(code, (str, int)) or str(code).strip() == "":
            continue
        name = str(item.get("name") or "")
        refs.append({"ref": str(code).strip(), "name": name})
    return refs


async def probe(spec: ExternalSourceSpec, creds: dict[str, Any]) -> SourceProbeResult:
    # Listing needs the LIST scope; a key that may only search still works, so a
    # failed listing must never fail the probe.
    try:
        refs = await list_refs(spec, creds)
    except ExternalSourceError:
        refs = []
    if not spec.ref:
        if not refs:
            raise ExternalSourceError(
                "INVALID_ARGUMENT",
                "no knowledge base selected and none visible to this credential",
                origin=ORIGIN,
            )
        return SourceProbeResult(
            ok=True,
            message="AKB reachable; pick one of the listed knowledge bases",
            refs=refs,
        )
    await search(spec, creds, "connectivity probe", top_k=1)
    return SourceProbeResult(ok=True, message="AKB retrieval endpoint reachable", refs=refs)
