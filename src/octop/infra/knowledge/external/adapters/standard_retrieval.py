"""Generic adapter for the standard external retrieval protocol.

Any third-party knowledge base implementing the contract can be connected:
``POST <endpoint>`` with ``Authorization: Bearer <key>`` plus
``X-Knowledge-Base-Id``, returning ``{"chunks": [{id, content, score, source,
metadata}]}`` where ``score`` is within ``[0, 1]``, higher is better.
"""

from __future__ import annotations

from typing import Any

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

ORIGIN = "standard-retrieval"


def _headers(spec: ExternalSourceSpec, creds: dict[str, Any]) -> dict[str, str]:
    api_key = str(creds.get("api_key") or creds.get("token") or "").strip()
    if not api_key:
        raise ExternalSourceError("INVALID_ARGUMENT", "API key is missing", origin=ORIGIN)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if spec.ref:
        headers["X-Knowledge-Base-Id"] = spec.ref
    return headers


async def search(
    spec: ExternalSourceSpec,
    creds: dict[str, Any],
    query: str,
    *,
    top_k: int,
) -> ExternalSearchResult:
    url = validate_endpoint(spec.endpoint, allow_insecure_http=spec.allow_insecure_http)
    capped = max(1, min(top_k, MAX_TOP_K))
    payload = await post_json(
        url,
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
    return SourceProbeResult(ok=True, message="retrieval endpoint reachable")
