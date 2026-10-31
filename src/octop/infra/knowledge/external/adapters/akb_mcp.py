"""AKB adapter over MCP JSON-RPC (stateless Streamable HTTP).

AKB exposes ``POST /api/mcp`` with no session and no server push, so every
call is a self-contained JSON-RPC exchange authenticated by ``X-API-Key``.
Business failures arrive as HTTP 200 with a JSON-RPC ``error`` or a tool
result flagged ``isError``, so transport status alone is not enough.
"""

from __future__ import annotations

import json
from typing import Any

from octop.infra.knowledge.akb_identity import OctopIdentity, issue_assertion
from octop.infra.knowledge.external.adapters._auth import auth_headers
from octop.infra.knowledge.external.http import (
    drop_api_prefix,
    post_json,
    validate_endpoint,
)
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
    base_argument,
)

ORIGIN = "akb-mcp"
_DEFAULT_TOOL = "knowledge_search"
_FALLBACK_TOOLS = ("knowledge_search", "knowledge_query")
# AKB negotiates 2025-06-18; the server's answer wins, this is only our offer.
_OFFERED_PROTOCOL = "2025-06-18"
# The MCP endpoint reuses the REST credential check, so a rejected transport
# call means the key or its scopes are the problem, not the payload.
_STATUS_MAP: dict[int, str] = {
    401: "INVALID_CREDENTIALS",
    403: "FORBIDDEN",
    429: "RATE_LIMITED",
}
# JSON-RPC error code → sanitized Octop code. ``-32001`` is AKB's
# "permission or scope insufficient".
_RPC_CODES: dict[int, str] = {-32001: "FORBIDDEN"}


def _url(spec: ExternalSourceSpec) -> str:
    base = validate_endpoint(spec.endpoint, allow_insecure_http=spec.allow_insecure_http)
    # Same trap as REST: a pasted /api/mcp endpoint would double it up.
    return drop_api_prefix(base, "/api/mcp") + "/api/mcp"


def _headers(spec: ExternalSourceSpec, creds: dict[str, Any]) -> dict[str, str]:
    headers = {
        **auth_headers(creds, origin=ORIGIN),
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if isinstance(spec.octop_identity, OctopIdentity) and not spec.octop_identity.workload_only:
        headers["X-Octop-Identity"] = issue_assertion(spec.octop_identity)
    return headers


async def _rpc(
    url: str,
    spec: ExternalSourceSpec,
    creds: dict[str, Any],
    method: str,
    params: dict[str, Any],
    *,
    timeout: float,
    request_id: int = 1,
) -> dict[str, Any]:
    # One assertion per HTTP request: AKB treats a repeated jti as a replay, so
    # the initialize and the tool call cannot share a header set.
    body = await post_json(
        url,
        headers=_headers(spec, creds),
        payload={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        timeout_seconds=timeout,
        origin=ORIGIN,
        status_map=_STATUS_MAP,
    )
    if not isinstance(body, dict):
        raise ExternalSourceError(
            "INVALID_JSON", "upstream returned an invalid envelope", origin=ORIGIN
        )
    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        # Upstream messages may echo internal state; only the code is surfaced.
        known = _RPC_CODES.get(code) if isinstance(code, int) else None
        raise ExternalSourceError(
            known or ("RPC_ERROR" if code is None else f"RPC_{code}"),
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
    # The source now names the tool it wants, discovered from the server's own
    # list; the built-in pair stays as the answer for older bindings that never
    # made that choice, so knowledge_search keeps working untouched.
    picked = (spec.tool_name or "").strip()
    if picked:
        return picked
    for tool in spec.read_tools:
        if tool in _FALLBACK_TOOLS:
            return tool
    return _DEFAULT_TOOL


def _arguments(spec: ExternalSourceSpec, query: str, top_k: int) -> dict[str, Any]:
    # What the call itself decides: the base is fixed by the binding, never
    # widened by the caller, and never missing — AKB turns a targetless
    # knowledge_query into an authorization failure rather than an empty answer.
    controlled = {
        "question": query,
        "query": query,
        "topK": top_k,
        **base_argument(spec),
    }
    # What the operator configured for the picked tool — extra filters a server
    # may ask for. Controlled keys win, so a stored value cannot redirect the
    # call to another base or another question, and the source's own forbidden
    # list stays in force no matter where the value came from.
    preset = {
        key: value
        for key, value in spec.tool_arguments.items()
        if str(key) not in spec.forbidden_arguments
    }
    return {**preset, **controlled}


async def search(
    spec: ExternalSourceSpec,
    creds: dict[str, Any],
    query: str,
    *,
    top_k: int,
) -> ExternalSearchResult:
    url = _url(spec)
    capped = max(1, min(top_k, MAX_TOP_K))
    await _rpc(
        url,
        spec,
        creds,
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
        spec,
        creds,
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


async def _initialize(spec: ExternalSourceSpec, creds: dict[str, Any]) -> dict[str, Any]:
    """Run the MCP handshake and return the server's full answer."""
    return await _rpc(
        _url(spec),
        spec,
        creds,
        "initialize",
        {
            "protocolVersion": _OFFERED_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "octop", "version": "1"},
        },
        timeout=spec.timeout_seconds,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    """One advertised tool, with the arguments it declares.

    The edit form renders these rather than guessing: every MCP server invents
    its own argument names, so the operator needs the server's own vocabulary.
    """
    schema = tool.get("inputSchema") or tool.get("input_schema")
    return {
        "name": str(tool.get("name") or "").strip(),
        "description": str(tool.get("description") or "").strip(),
        "input_schema": schema if isinstance(schema, dict) else {},
    }


async def list_refs(spec: ExternalSourceSpec, creds: dict[str, Any]) -> list[dict[str, str]]:
    """List the knowledge bases this credential may read (``list_knowledge_bases``)."""
    await _initialize(spec, creds)
    bases = await _rpc(
        _url(spec),
        spec,
        creds,
        "tools/call",
        {"name": "list_knowledge_bases", "arguments": {}},
        timeout=spec.timeout_seconds,
        request_id=2,
    )
    refs: list[dict[str, str]] = []
    for item in _collect_items(bases):
        if not isinstance(item, dict):
            continue
        code = item.get("baseCode") or item.get("knowledgeBaseCode") or item.get("id")
        name = item.get("name") or item.get("displayName") or ""
        if isinstance(code, (str, int)) and str(code).strip():
            refs.append({"ref": str(code).strip(), "name": str(name)})
    return refs


async def probe(spec: ExternalSourceSpec, creds: dict[str, Any]) -> SourceProbeResult:
    url = _url(spec)
    hello = await _initialize(spec, creds)
    negotiated = hello.get("protocolVersion")
    negotiated = negotiated if isinstance(negotiated, str) else ""
    # What the server advertises at handshake: it tells the operator whether
    # they reached the intended deployment and which tools it offers.
    server_info = _as_dict(hello.get("serverInfo"))
    capabilities = _as_dict(hello.get("capabilities"))
    raw_instructions = hello.get("instructions")
    instructions = raw_instructions if isinstance(raw_instructions, str) else ""
    listed = await _rpc(
        url, spec, creds, "tools/list", {}, timeout=spec.timeout_seconds, request_id=2
    )
    tools_raw = listed.get("tools")
    entries = [
        tool
        for tool in (tools_raw if isinstance(tools_raw, list) else [])
        if isinstance(tool, dict)
    ]
    tools = [str(tool.get("name")) for tool in entries if isinstance(tool.get("name"), str)]
    schemas = [_tool_schema(tool) for tool in entries]
    picked = (spec.tool_name or "").strip()
    # A custom tool counts once the source picked it; until then discovery only
    # cares about the built-in pair, which is what an unbound source relies on.
    usable = [tool for tool in tools if tool in _FALLBACK_TOOLS or tool == picked]
    if not usable:
        return SourceProbeResult(
            ok=False,
            message="AKB granted no search or query tool",
            tools=tools,
            tool_schemas=schemas,
            protocol_version=negotiated,
            server_info=server_info,
            capabilities=capabilities,
            instructions=instructions,
        )
    refs: list[dict[str, str]] = []
    if "list_knowledge_bases" in tools:
        try:
            refs = await list_refs(spec, creds)
        except ExternalSourceError:
            refs = []
    suffix = f" (protocol {negotiated})" if negotiated else ""
    return SourceProbeResult(
        ok=True,
        message=f"AKB reachable{suffix}",
        tools=tools,
        tool_schemas=schemas,
        refs=refs,
        protocol_version=negotiated,
        server_info=server_info,
        capabilities=capabilities,
        instructions=instructions,
    )
