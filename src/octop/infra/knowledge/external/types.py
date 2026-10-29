"""Value types shared by every external knowledge source adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SOURCE_KINDS: tuple[str, ...] = (
    "akb-mcp",
    "akb-rest",
    "ragflow",
    "weknora",
    "standard-retrieval",
)

DEFAULT_TOP_K = 8
MAX_TOP_K = 50
MAX_TIMEOUT_SECONDS = 60.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_CHUNK_CHARS = 1200


class ExternalSourceError(RuntimeError):
    """Sanitized upstream failure: never carries credentials or response bodies."""

    def __init__(self, code: str, message: str, *, origin: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.origin = origin


@dataclass(frozen=True)
class ExternalChunk:
    """One normalized passage, independent of the upstream source system."""

    id: str
    content: str
    score: float
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)
    origin: str = ""
    ref: str = ""

    def dedupe_key(self) -> str:
        return self.id.strip() or self.content[:200].strip()


@dataclass(frozen=True)
class SourceWarning:
    """A single source that failed or was skipped; retrieval continues."""

    origin: str
    code: str
    message: str


@dataclass(frozen=True)
class ExternalSearchResult:
    chunks: list[ExternalChunk] = field(default_factory=list)
    warnings: list[SourceWarning] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SourceProbeResult:
    """Outcome of a connectivity + capability check."""

    ok: bool
    message: str = ""
    tools: list[str] = field(default_factory=list)
    refs: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ExternalSourceSpec:
    """Runtime settings for one external knowledge source binding."""

    binding_id: str
    display_name: str
    kind: str
    endpoint: str
    ref: str = ""
    top_k: int = DEFAULT_TOP_K
    min_score: float = 0.0
    timeout_seconds: float = 10.0
    allow_insecure_http: bool = False
    selector_argument: str = ""
    forbidden_arguments: tuple[str, ...] = ()
    read_tools: tuple[str, ...] = ()
    enabled: bool = True
    instance_id: str = ""
    owner_user_id: int = 0
    shared: bool = False
    created_at: int = 0
    updated_at: int = 0
    # Fresh per-call identity assertion (AKB ``X-Octop-Identity``); empty when
    # delegated identity is not configured for this instance.
    identity_token: str = ""
