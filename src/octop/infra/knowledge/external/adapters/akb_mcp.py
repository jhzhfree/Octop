"""AKB adapter over MCP JSON-RPC (stateless Streamable HTTP).

AKB exposes ``POST /api/mcp`` with no session and no server push, so every
call is a self-contained JSON-RPC exchange authenticated by ``X-API-Key``.
Business failures arrive as HTTP 200 with a JSON-RPC ``error`` or a tool
result flagged ``isError``, so transport status alone is not enough.
"""

from __future__ import annotations

import json
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

ORIGIN = "akb-mcp"
_DEFAULT_TOOL = "knowledge_search"
_FALLBACK_TOOLS = ("knowledge_search", "knowledge_query")
# AKB negotiates 2025-06-18; the server's answer wins, this is only our offer.
_OFFERED_PROTOCOL = "2025-06-18"


def _url(spec: ExternalSourceSpec) -> str:
    base = validate_endpoint(spec.endpoint, allow_insecure_http=spec.allow_insecure_http)
    return base + "/api/mcp"


def _headers(creds: dict[str, Any]) -> dict[str, str]:
    api_key = str(creds.get("api_key") or "").strip()
    if not api_key:
        raise ExternalSourceError("INVALID_ARGUMENT", "AKB API key is missing", origin=ORIGIN)
    return {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


async def _rpc(
    url: str,
    headers: dict[str, str],
    method: str,
    params: dict[str, Any],
    *,
    timeout: float,
    request_id: int = 1,
) -> dict[str, Any]:
    body = await post_json(
        url,
        headers=headers,
        payload={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        timeout_seconds=timeout,
        origin=ORIGIN,
    )
    if not isinstance(body, dict):
        raise ExternalSourceError(
            "INVALID_JSON", "upstream returned an invalid envelope", origin=ORIGIN
        )
    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        # Upstream messages may echo internal state; only the code is surfaced.
        raise ExternalSourceError(
            "RPC_ERROR" if code is None else f"RPC_{code}",
            "upstream rejected the request",
            origin=ORIGIN,
        )
    result = body.get("result")
    if not isinstance(result, dict):
        raise ExternalSourceError("INVALID_JSON", "upstream returned no result", origin=ORIGIN)
    return result


def _collect_items(result: dict[str, Any]) -> list[Any]:
    """Pull the passage array out of an MCP tool result."""
    structured = result.get("structuredContent")
    if isinstance(structured, (list, dict)):
        items = extract_items(structured)
        if items:
            return items
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            items = extract_items(parsed)
            if items:
                return items
    return extract_items(result)


def _tool_name(spec: ExternalSourceSpec) -> str:
    for tool in spec.read_tools:
        if tool in _FALLBACK_TOOLS:
            return tool
    return _DEFAULT_TOOL


def _arguments(spec: ExternalSourceSpec, query: str, top_k: int) -> dict[str, Any]:
    args: dict[str, Any] = {"question": query, "query": query, "topK": top_k}
    # The knowledge base is fixed by the binding: never let a caller widen it.
    if spec.ref:
        args["knowledgeBaseCode"] = spec.ref
    return args


async def search(
    spec: ExternalSourceSpec,
    creds: dict[str, Any],
    query: str,
    *,
    top_k: int,
) -> ExternalSearchResult:
    url = _url(spec)
    headers = _headers(creds)
    capped = max(1, min(top_k, MAX_TOP_K))
    await _rpc(
        url,
        headers,
        "initialize",
        {
            "protocolVersion": _OFFERED_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "octop", "version": "1"},
        },
        timeout=spec.timeout_seconds,
    )
    result = await _rpc(
        url,
        headers,
        "tools/call",
        {"name": _tool_name(spec), "arguments": _arguments(spec, query, capped)},
        timeout=spec.timeout_seconds,
        request_id=2,
    )
    if result.get("isError") is True:
        raise ExternalSourceError("BUSINESS_ERROR", "upstream reported a tool error", origin=ORIGIN)
    chunks = normalize_chunks(_collect_items(result), origin=ORIGIN, ref=spec.ref)
    return ExternalSearchResult(
        chunks=filter_chunks(chunks, min_score=spec.min_score, limit=capped)
    )


async def probe(spec: ExternalSourceSpec, creds: dict[str, Any]) -> SourceProbeResult:
    url = _url(spec)
    headers = _headers(creds)
    result = await _rpc(
        url,
        headers,
        "initialize",
        {
            "protocolVersion": _OFFERED_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "octop", "version": "1"},
        },
        timeout=spec.timeout_seconds,
    )
    negotiated = result.get("protocolVersion")
    listed = await _rpc(url, headers, "tools/list", {}, timeout=spec.timeout_seconds, request_id=2)
    tools_raw = listed.get("tools")
    tools = [
        str(tool.get("name"))
        for tool in (tools_raw if isinstance(tools_raw, list) else [])
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    ]
    usable = [tool for tool in tools if tool in _FALLBACK_TOOLS]
    if not usable:
        return SourceProbeResult(
            ok=False,
            message="AKB granted no search or query tool",
            tools=tools,
        )
    refs: list[dict[str, str]] = []
    if "list_knowledge_bases" in tools:
        try:
            bases = await _rpc(
                url,
                headers,
                "tools/call",
                {"name": "list_knowledge_bases", "arguments": {}},
                timeout=spec.timeout_seconds,
                request_id=3,
            )
            for item in _collect_items(bases):
                if not isinstance(item, dict):
                    continue
                code = item.get("code") or item.get("knowledgeBaseCode") or item.get("id")
                name = item.get("name") or item.get("displayName") or ""
                if isinstance(code, str) and code:
                    refs.append({"ref": code, "name": str(name)})
        except ExternalSourceError:
            refs = []
    suffix = f" (protocol {negotiated})" if isinstance(negotiated, str) else ""
    return SourceProbeResult(ok=True, message=f"AKB reachable{suffix}", tools=tools, refs=refs)
