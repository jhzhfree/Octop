"""External knowledge sources: connect third-party knowledge bases to Octop."""

from __future__ import annotations

from octop.infra.knowledge.external.fuse import rrf_fuse
from octop.infra.knowledge.external.normalize import (
    coerce_score,
    dedupe_chunks,
    filter_chunks,
    normalize_chunks,
)
from octop.infra.knowledge.external.registry import get_adapter, supported_kinds
from octop.infra.knowledge.external.types import (
    ExternalChunk,
    ExternalSearchResult,
    ExternalSourceError,
    ExternalSourceSpec,
    SourceProbeResult,
    SourceWarning,
)

__all__ = [
    "ExternalChunk",
    "ExternalSearchResult",
    "ExternalSourceError",
    "ExternalSourceSpec",
    "SourceProbeResult",
    "SourceWarning",
    "coerce_score",
    "dedupe_chunks",
    "filter_chunks",
    "get_adapter",
    "normalize_chunks",
    "rrf_fuse",
    "supported_kinds",
]
