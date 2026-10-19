"""Enforce approved product knowledge-source bindings at MCP tool-call time."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

CONFIG_KEY = "octop_knowledge_sources"


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
        source
        for source in matching_sources
        if len(str(source["mcp_server"])) == longest_server
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
        if isinstance(source.get("selector_argument"), str)
        and source.get("selector_argument")
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
    allowed_refs = {
        str(source.get("knowledge_base_ref") or "")
        for source in allowed_sources
    }
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


class KnowledgeSourceScopeMiddleware(AgentMiddleware[Any, Any]):
    """Apply run-scoped knowledge source bindings from trusted LangGraph context."""

    def _rewrite(self, request: ToolCallRequest) -> tuple[ToolCallRequest, str | None]:
        try:
            configurable = dict(get_config().get("configurable") or {})
        except RuntimeError:
            configurable = {}
        call = request.tool_call
        rewritten, error = enforce_knowledge_source_args(
            str(call.get("name") or ""),
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


__all__ = ["CONFIG_KEY", "KnowledgeSourceScopeMiddleware", "enforce_knowledge_source_args"]
