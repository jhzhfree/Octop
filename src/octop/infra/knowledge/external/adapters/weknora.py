"""WeKnora adapter — reuses the existing connector gateway adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from octop.infra.connectors.gateway.adapters import weknora as weknora_gateway
from octop.infra.knowledge.external.normalize import (
    dedupe_chunks,
    extract_items,
    filter_chunks,
    normalize_chunks,
)
from octop.infra.knowledge.external.types import (
    ExternalSearchResult,
    ExternalSourceError,
    ExternalSourceSpec,
    SourceProbeResult,
)

ORIGIN = "weknora"
_GATEWAY_MAX_RESULTS = 8


def _creds(creds: dict[str, Any]) -> dict[str, Any]:
    base_url = str(creds.get("base_url") or "").strip()
    if not base_url:
        raise ExternalSourceError("INVALID_ARGUMENT", "WeKnora base URL is missing", origin=ORIGIN)
    return {
        "base_url": base_url,
        "api_key": str(creds.get("api_key") or "").strip(),
        "tenant_id": str(creds.get("tenant_id") or "").strip(),
    }


async def search(
    spec: ExternalSourceSpec,
    creds: dict[str, Any],
    query: str,
    *,
    top_k: int,
) -> ExternalSearchResult:
    capped = max(1, min(top_k, _GATEWAY_MAX_RESULTS))
    args: dict[str, Any] = {"query": query, "max_results": capped}
    if spec.ref:
        args["knowledge_base_ids"] = [spec.ref]

    def _call() -> str:
        return weknora_gateway.call_tool(_creds(creds), "search", args)

    try:
        raw = await asyncio.to_thread(_call)
    except ValueError as exc:
        raise ExternalSourceError(
            "UPSTREAM_ERROR", "WeKnora request failed", origin=ORIGIN
        ) from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ExternalSourceError(
            "INVALID_JSON", "WeKnora returned invalid JSON", origin=ORIGIN
        ) from exc
    items = extract_items(payload, keys=("chunks", "results", "passages"))
    chunks = dedupe_chunks(normalize_chunks(items, origin=ORIGIN, ref=spec.ref))
    return ExternalSearchResult(
        chunks=filter_chunks(chunks, min_score=spec.min_score, limit=capped)
    )


async def probe(spec: ExternalSourceSpec, creds: dict[str, Any]) -> SourceProbeResult:
    def _call() -> None:
        weknora_gateway.probe_credentials(_creds(creds))

    try:
        await asyncio.to_thread(_call)
    except ValueError:
        return SourceProbeResult(ok=False, message="WeKnora rejected the credentials")
    return SourceProbeResult(ok=True, message="WeKnora reachable", tools=["search"])
