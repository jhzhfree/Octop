"""Per-turn enrichment of the search_knowledge tool description."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.tools.base import BaseTool
from langgraph.config import get_config

from octop.infra.knowledge.tools import SEARCH_KNOWLEDGE_TOOL, format_search_knowledge_description


def catalog_for_selected_bases(
    bases: Sequence[Any],
    selected_ids: Sequence[str],
) -> list[dict[str, str]]:
    """Build ``{id, name, description}`` rows for visible selected bases."""
    by_id = {str(getattr(base, "id", "")): base for base in bases}
    catalog: list[dict[str, str]] = []
    for kb_id in selected_ids:
        key = str(kb_id).strip()
        if not key:
            continue
        base = by_id.get(key)
        if base is None:
            continue
        raw_name = getattr(base, "name", "")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        raw_desc = getattr(base, "description", "")
        description = raw_desc.strip() if isinstance(raw_desc, str) else ""
        catalog.append(
            {
                "id": key,
                "name": name or key,
                "description": description,
            }
        )
    return catalog


def _catalog_from_config() -> list[dict[str, str]]:
    """Internal bases plus externally mounted sources, both from the turn config.

    An expert can mount external sources without selecting any internal base,
    so the external catalog has to count as "something is attached" —
    otherwise the tool would be hidden and the mount silently unreachable.
    """
    cfg = get_config().get("configurable") or {}
    out: list[dict[str, str]] = []
    for key in ("knowledge_base_catalog", "external_knowledge_catalog"):
        raw = cfg.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            item_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip() or item_id
            description = str(item.get("description") or "").strip()
            if not name:
                continue
            out.append({"id": item_id or name, "name": name, "description": description})
    return out


def _with_enriched_tool_description(request: ModelRequest[Any]) -> ModelRequest[Any]:
    catalog = _catalog_from_config()
    tools_in = list(request.tools or [])
    if not tools_in:
        return request

    if not catalog:
        filtered = [
            tool
            for tool in tools_in
            if not (isinstance(tool, BaseTool) and tool.name == SEARCH_KNOWLEDGE_TOOL)
        ]
        if len(filtered) == len(tools_in):
            return request
        return request.override(tools=filtered)

    description = format_search_knowledge_description(catalog)
    tools_out: list[Any] = []
    changed = False
    for tool in tools_in:
        if isinstance(tool, BaseTool) and tool.name == SEARCH_KNOWLEDGE_TOOL:
            if tool.description == description:
                tools_out.append(tool)
            else:
                tools_out.append(tool.model_copy(update={"description": description}))
                changed = True
            continue
        tools_out.append(tool)
    if not changed:
        return request
    return request.override(tools=tools_out)


class KnowledgeSearchHintMiddleware(AgentMiddleware[Any, Any]):
    """Rewrite ``search_knowledge`` description with this turn's KB titles/descriptions.

    Hide the tool entirely when nothing is attached — a "do not call" note in
    the schema is weaker than omitting the tool from the model's tool list.
    """

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(_with_enriched_tool_description(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(_with_enriched_tool_description(request))


__all__ = [
    "KnowledgeSearchHintMiddleware",
    "catalog_for_selected_bases",
]
