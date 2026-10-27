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
    """Adapter backed by the module-level ``search`` / ``probe`` coroutines."""

    origin: str
    _search: Callable[..., Awaitable[ExternalSearchResult]]
    _probe: Callable[..., Awaitable[SourceProbeResult]]

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
