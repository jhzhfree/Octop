"""kind → adapter registry for external knowledge sources."""

from __future__ import annotations

from octop.infra.knowledge.external.adapters import (
    FunctionAdapter,
    akb_mcp,
    akb_rest,
    ragflow,
    standard_retrieval,
    weknora,
)
from octop.infra.knowledge.external.adapters.base import ExternalKnowledgeAdapter
from octop.infra.knowledge.external.types import SOURCE_KINDS

_ADAPTERS: dict[str, FunctionAdapter] = {
    "akb-mcp": FunctionAdapter(
        origin=akb_mcp.ORIGIN,
        _search=akb_mcp.search,
        _probe=akb_mcp.probe,
        _refs=akb_mcp.list_refs,
    ),
    "akb-rest": FunctionAdapter(
        origin=akb_rest.ORIGIN,
        _search=akb_rest.search,
        _probe=akb_rest.probe,
        _refs=akb_rest.list_refs,
    ),
    "standard-retrieval": FunctionAdapter(
        origin=standard_retrieval.ORIGIN,
        _search=standard_retrieval.search,
        _probe=standard_retrieval.probe,
    ),
    "ragflow": FunctionAdapter(origin=ragflow.ORIGIN, _search=ragflow.search, _probe=ragflow.probe),
    "weknora": FunctionAdapter(origin=weknora.ORIGIN, _search=weknora.search, _probe=weknora.probe),
}


def get_adapter(kind: str) -> ExternalKnowledgeAdapter | None:
    adapter = _ADAPTERS.get(kind)
    return adapter if adapter is not None else None


def supported_kinds() -> list[str]:
    return [kind for kind in SOURCE_KINDS if kind in _ADAPTERS]
