"""Product-neutral digital-worker knowledge-source contracts."""

from __future__ import annotations

from typing import Any

AKB_READ_TOOLS = ("knowledge_query", "knowledge_search")
AKB_MEMORY_READ_TOOLS = ("memory_recall",)


def canonical_knowledge_sources(capabilities: Any) -> list[dict[str, Any]]:
    """Read generic bindings and upgrade persisted AKB-only bindings in memory."""
    if not isinstance(capabilities, dict):
        return []
    sources = capabilities.get("knowledge_sources")
    if not isinstance(sources, list) or (not sources and capabilities.get("akb_knowledge_sources")):
        sources = capabilities.get("akb_knowledge_sources", [])
    normalized: list[dict[str, Any]] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        provider = str(source.get("provider") or "").strip()
        mcp_server = str(source.get("mcp_server") or "").strip()
        base_ref = str(
            source.get("knowledge_base_ref") or source.get("knowledge_base_code") or ""
        ).strip()
        if not provider or not mcp_server or not base_ref:
            continue
        selector_argument = source.get("selector_argument")
        if selector_argument is None and provider == "akb":
            selector_argument = "knowledgeBaseCode"
        read_tools = source.get("read_tools")
        if not isinstance(read_tools, list):
            read_tools = list(AKB_READ_TOOLS) if provider == "akb" else []
        forbidden_arguments = source.get("forbidden_arguments")
        if not isinstance(forbidden_arguments, list):
            forbidden_arguments = ["knowledgeBaseId"] if provider == "akb" else []
        normalized.append(
            {
                "provider": provider,
                "mcp_server": mcp_server,
                "knowledge_base_ref": base_ref,
                "selector_argument": selector_argument,
                "read_tools": read_tools,
                "forbidden_arguments": forbidden_arguments,
                "access": "read",
            }
        )
    return normalized


def legacy_akb_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project generic AKB bindings to the response shape used by older clients."""
    return [
        {
            "provider": "akb",
            "mcp_server": source.get("mcp_server"),
            "knowledge_base_code": source.get(
                "knowledge_base_ref", source.get("knowledge_base_code")
            ),
            "access": "read",
        }
        for source in sources
        if source.get("provider") == "akb"
    ]


def canonical_memory_sources(capabilities: Any) -> list[dict[str, Any]]:
    """Return product-neutral, read-only Agent-memory bindings."""
    if not isinstance(capabilities, dict):
        return []
    sources = capabilities.get("memory_sources")
    normalized: list[dict[str, Any]] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        provider = str(source.get("provider") or "").strip()
        mcp_server = str(source.get("mcp_server") or "").strip()
        scope_type = str(source.get("scope_type") or "AGENT").strip().upper()
        if (
            not provider
            or not mcp_server
            or scope_type not in {"AGENT", "TEAM", "PROJECT", "TASK", "WORKFLOW", "SESSION"}
        ):
            continue
        tools = source.get("read_tools")
        if not isinstance(tools, list):
            tools = list(AKB_MEMORY_READ_TOOLS) if provider == "akb" else []
        normalized.append(
            {
                "provider": provider,
                "mcp_server": mcp_server,
                "scope_type": scope_type,
                "scope_code": str(source.get("scope_code") or "").strip() or None,
                "read_tools": tools,
                "access": "read",
            }
        )
    return normalized
