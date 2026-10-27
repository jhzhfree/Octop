"""Adapter contract for one external knowledge system."""

from __future__ import annotations

from typing import Any, Protocol

from octop.infra.knowledge.external.types import (
    ExternalSearchResult,
    ExternalSourceSpec,
    SourceProbeResult,
)


class ExternalKnowledgeAdapter(Protocol):
    async def search(
        self,
        spec: ExternalSourceSpec,
        creds: dict[str, Any],
        query: str,
        *,
        top_k: int,
    ) -> ExternalSearchResult: ...

    async def probe(
        self,
        spec: ExternalSourceSpec,
        creds: dict[str, Any],
    ) -> SourceProbeResult: ...
