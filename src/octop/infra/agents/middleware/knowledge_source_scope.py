"""Enforce approved product knowledge-source bindings at MCP tool-call time."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

CONFIG_KEY = "octop_knowledge_sources"
MEMORY_CONFIG_KEY = "octop_memory_sources"
MEMORY_SERVERS_CONFIG_KEY = "octop_memory_servers"


def hide_memory_write_tools(tools: Any, servers: Any) -> list[Any]:
    """Remove write/delete tools for bound memory servers from model-visible tools."""
    if not isinstance(tools, list) or not isinstance(servers, list):
        return list(tools) if isinstance(tools, list) else []
    bound_servers = [server for server in servers if isinstance(server, str) and server]
    if not bound_servers:
        return list(tools)
    hidden = {"memory_capture", "memory_forget"}
    visible: list[Any] = []
    for tool in tools:
        name = tool.get("name") if isinstance(tool, Mapping) else getattr(tool, "name", None)
        name = str(name or "")
        if any(
            name.startswith(f"{server}_") and name.removeprefix(f"{server}_") in hidden
            for server in bound_servers
        ):
            continue
        visible.append(tool)
    return visible


def enforce_knowledge_source_args(
    tool_name: str, args: Any, sources: Any
) -> tuple[dict[str, Any] | None, str | None]:
    """Apply the read-tool allowlist and selector for a bound MCP server."""
    if not isinstance(sources, list):
        return None, None
    matching_sources = [
        source
        for source in sources
        if isinstance(source, dict)
        and isinstance(source.get("mcp_server"), str)
        and tool_name.startswith(f"{source['mcp_server']}_")
    ]
    if not matching_sources:
        return None, None
    longest_server = max(len(str(source["mcp_server"])) for source in matching_sources)
    server_sources = [
        source for source in matching_sources if len(str(source["mcp_server"])) == longest_server
    ]
    prefix = f"{server_sources[0]['mcp_server']}_"
    tool = tool_name.removeprefix(prefix)
    allowed_sources = [
        source
        for source in server_sources
        if isinstance(source.get("read_tools"), list) and tool in source["read_tools"]
    ]
    if not allowed_sources:
        return None, "This knowledge connection only permits its approved read tools"
    if not isinstance(args, Mapping):
        return None, "Knowledge tool arguments must be an object"
    rewritten = dict(args)
    forbidden_arguments = {
        str(argument)
        for source in allowed_sources
        if isinstance(source.get("forbidden_arguments"), list)
        for argument in source["forbidden_arguments"]
    }
    if any(argument in rewritten for argument in forbidden_arguments):
        return None, "Knowledge request contains a disallowed selector argument"
    selector_arguments = {
        str(source.get("selector_argument"))
        for source in allowed_sources
        if isinstance(source.get("selector_argument"), str) and source.get("selector_argument")
    }
    if not selector_arguments:
        refs = {str(source.get("knowledge_base_ref") or "") for source in allowed_sources}
        if len(refs) > 1:
            return None, "Knowledge request must select an approved knowledge-base reference"
        return rewritten, None
    if len(selector_arguments) != 1 or any(
        not source.get("selector_argument") for source in allowed_sources
    ):
        return None, "Knowledge selector configuration is ambiguous"
    selector = next(iter(selector_arguments))
    allowed_refs = {str(source.get("knowledge_base_ref") or "") for source in allowed_sources}
    allowed_refs.discard("")
    if not allowed_refs:
        return None, "Knowledge-base binding is invalid"
    requested_ref = rewritten.get(selector)
    if requested_ref is not None and requested_ref not in allowed_refs:
        return None, "Knowledge request is outside the approved knowledge-base scope"
    if requested_ref is None:
        if len(allowed_refs) != 1:
            return None, "Knowledge request must specify an approved knowledge-base reference"
        rewritten[selector] = next(iter(allowed_refs))
    return rewritten, None


def enforce_memory_source_args(
    tool_name: str, args: Any, sources: Any, servers: Any = None
) -> tuple[dict[str, Any] | None, str | None]:
    """Allow only scoped memory recall for a bound platform memory server."""
    if not isinstance(sources, list):
        sources = []
    if not isinstance(servers, list):
        servers = [
            str(source.get("mcp_server"))
            for source in sources
            if isinstance(source, dict) and source.get("mcp_server")
        ]
    bound_servers = [server for server in servers if isinstance(server, str) and server]
    if not bound_servers:
        return None, None
    matching_servers = [server for server in bound_servers if tool_name.startswith(f"{server}_")]
    if not matching_servers:
        return None, None
    server = max(matching_servers, key=len)
    prefix = f"{server}_"
    tool = tool_name.removeprefix(prefix)
    # This integration contract deliberately exposes only the common recall tool.
    # Other knowledge tools on a shared MCP server remain governed by knowledge bindings.
    memory_tools = {"memory_recall", "memory_capture", "memory_forget"}
    if tool not in memory_tools:
        return None, None
    server_sources = [
        source
        for source in sources
        if isinstance(source, dict) and source.get("mcp_server") == server
    ]
    allowed = [
        source
        for source in server_sources
        if source.get("access") == "read"
        and isinstance(source.get("read_tools"), list)
        and tool in source["read_tools"]
    ]
    if tool != "memory_recall" or not allowed:
        return None, "This memory connection only permits approved read-only recall"
    if not isinstance(args, Mapping):
        return None, "Memory tool arguments must be an object"
    scopes = {
        (
            str(source.get("scope_type") or "AGENT").upper(),
            str(source.get("scope_code") or ""),
        )
        for source in allowed
    }
    rewritten = dict(args)
    requested_type = str(rewritten.get("scopeType") or "").upper()
    requested_code = str(rewritten.get("scopeCode") or "")
    if len(scopes) == 1:
        scope_type, scope_code = next(iter(scopes))
    else:
        if not requested_type:
            return None, "Memory request must select one approved scope"
        scope_type, scope_code = requested_type, requested_code
        if (scope_type, scope_code) not in scopes:
            return None, "Memory request is outside the approved scope bindings"
    rewritten["scopeType"] = scope_type
    if scope_type == "AGENT":
        rewritten.pop("scopeCode", None)
    elif scope_code:
        rewritten["scopeCode"] = scope_code
    else:
        return None, "Bound memory scope has no scope code"
    return rewritten, None


class KnowledgeSourceScopeMiddleware(AgentMiddleware[Any, Any]):
    """Apply run-scoped knowledge source bindings from trusted LangGraph context."""

    def _visible_model_request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        try:
            configurable = dict(get_config().get("configurable") or {})
        except RuntimeError:
            configurable = {}
        tools = hide_memory_write_tools(request.tools, configurable.get(MEMORY_SERVERS_CONFIG_KEY))
        return request.override(tools=tools) if tools != request.tools else request

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._visible_model_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._visible_model_request(request))

    def _rewrite(self, request: ToolCallRequest) -> tuple[ToolCallRequest, str | None]:
        try:
            configurable = dict(get_config().get("configurable") or {})
        except RuntimeError:
            configurable = {}
        call = request.tool_call
        tool_name = str(call.get("name") or "")
        if any(
            tool_name.endswith(f"_{tool}")
            for tool in ("memory_recall", "memory_capture", "memory_forget")
        ):
            rewritten, error = enforce_memory_source_args(
                tool_name,
                call.get("args"),
                configurable.get(MEMORY_CONFIG_KEY),
                configurable.get(MEMORY_SERVERS_CONFIG_KEY),
            )
        else:
            rewritten, error = enforce_knowledge_source_args(
                tool_name,
                call.get("args"),
                configurable.get(CONFIG_KEY),
            )
        if rewritten is not None:
            request = request.override(tool_call={**call, "args": rewritten})
        return request, error

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        request, error = self._rewrite(request)
        if error:
            return ToolMessage(
                content=error,
                tool_call_id=str(request.tool_call.get("id") or ""),
                status="error",
            )
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        request, error = self._rewrite(request)
        if error:
            return ToolMessage(
                content=error,
                tool_call_id=str(request.tool_call.get("id") or ""),
                status="error",
            )
        return await handler(request)


__all__ = [
    "CONFIG_KEY",
    "MEMORY_CONFIG_KEY",
    "MEMORY_SERVERS_CONFIG_KEY",
    "KnowledgeSourceScopeMiddleware",
    "hide_memory_write_tools",
    "enforce_knowledge_source_args",
    "enforce_memory_source_args",
]
