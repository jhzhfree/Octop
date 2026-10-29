"""Adapters for every supported external knowledge system."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from octop.infra.knowledge.external.types import (
    ExternalSearchResult,
    ExternalSourceSpec,
    SourceProbeResult,
)


@dataclass(frozen=True)
class FunctionAdapter:
    """Adapter backed by the module-level ``search`` / ``probe`` coroutines.

    ``_refs`` is optional: sources that can enumerate their knowledge bases
    expose it so the UI can offer a picklist instead of a free-text ref.
    """

    origin: str
    _search: Callable[..., Awaitable[ExternalSearchResult]]
    _probe: Callable[..., Awaitable[SourceProbeResult]]
    _refs: Callable[..., Awaitable[list[dict[str, str]]]] | None = None

    async def search(
        self,
        spec: ExternalSourceSpec,
        creds: dict[str, Any],
        query: str,
        *,
        top_k: int,
    ) -> ExternalSearchResult:
        return await self._search(spec, creds, query, top_k=top_k)

    async def probe(self, spec: ExternalSourceSpec, creds: dict[str, Any]) -> SourceProbeResult:
        return await self._probe(spec, creds)

    async def list_refs(
        self, spec: ExternalSourceSpec, creds: dict[str, Any]
    ) -> list[dict[str, str]]:
        if self._refs is None:
            return []
        return await self._refs(spec, creds)
