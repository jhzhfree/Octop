"""AKB adapter over the versioned REST retrieval endpoint.

Uses the standard retrieval contract (``POST /api/v1/external/kb/retrievals``)
with AKB's ``X-API-Key`` header, for deployments where MCP is disabled.
"""

from __future__ import annotations

from typing import Any

from octop.infra.knowledge.akb_identity import OctopIdentity, issue_assertion
from octop.infra.knowledge.external.http import post_json, validate_endpoint
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


def _url(spec: ExternalSourceSpec) -> str:
    base = validate_endpoint(spec.endpoint, allow_insecure_http=spec.allow_insecure_http)
    return base + "/api/v1/external/kb/retrievals"


def _headers(spec: ExternalSourceSpec, creds: dict[str, Any]) -> dict[str, str]:
    api_key = str(creds.get("api_key") or "").strip()
    if not api_key:
        raise ExternalSourceError("INVALID_ARGUMENT", "AKB API key is missing", origin=ORIGIN)
    if not spec.ref:
        raise ExternalSourceError(
            "INVALID_ARGUMENT", "an AKB knowledge base reference is required", origin=ORIGIN
        )
    headers = {
        "X-API-Key": api_key,
        "X-Knowledge-Base-Id": spec.ref,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if isinstance(spec.octop_identity, OctopIdentity):
        headers["X-Octop-Identity"] = issue_assertion(spec.octop_identity)
    return headers


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
        headers=_headers(spec, creds),
        payload={"question": query, "topK": capped},
        timeout_seconds=spec.timeout_seconds,
        origin=ORIGIN,
    )
    items = extract_items(payload, keys=("chunks",))
    chunks = normalize_chunks(items, origin=ORIGIN, ref=spec.ref)
    return ExternalSearchResult(
        chunks=filter_chunks(chunks, min_score=spec.min_score, limit=capped)
    )


async def probe(spec: ExternalSourceSpec, creds: dict[str, Any]) -> SourceProbeResult:
    await search(spec, creds, "connectivity probe", top_k=1)
    return SourceProbeResult(ok=True, message="AKB retrieval endpoint reachable")
