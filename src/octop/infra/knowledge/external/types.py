"""Value types shared by every external knowledge source adapter."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

SOURCE_KINDS: tuple[str, ...] = (
    "akb-mcp",
    "akb-rest",
    "ragflow",
    "weknora",
    "standard-retrieval",
)

# Ways an AKB source may present its API key; the first one is the default.
API_KEY_HEADERS: tuple[str, ...] = ("x-api-key", "authorization")

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
    source_id: str = ""

    def dedupe_key(self) -> str:
        return self.id.strip() or self.content[:200].strip()


@dataclass(frozen=True)
class SourceWarning:
    """A single source that failed or was skipped; retrieval continues."""

    origin: str
    code: str
    message: str
    source_id: str = ""


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
    # MCP handshake details, so an operator sees what the server advertises.
    protocol_version: str = ""
    server_info: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    instructions: str = ""
    # What each discovered tool accepts. The edit form renders a tool's own
    # schema instead of guessing which arguments an arbitrary MCP tool takes.
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)


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
    # MCP servers publish whatever tools they like, so the operator picks one
    # from the advertised list and fills in what that tool needs.
    tool_name: str = ""
    tool_arguments: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    instance_id: str = ""
    owner_user_id: int = 0
    shared: bool = False
    created_at: int = 0
    updated_at: int = 0
    # Per-call Octop delegation is injected only from trusted server runtime context.
    octop_identity: Any = None


# AKB has one public selector: the generated KB_ identifier.
DEFAULT_SELECTOR_ARGUMENT = "knowledgeBaseId"
ID_SELECTOR_ARGUMENT = "knowledgeBaseId"
INVALID_ARGUMENT = "INVALID_ARGUMENT"
MISSING_REF_MESSAGE = "an AKB knowledge base reference is required"


def missing_ref(origin: str) -> ExternalSourceError:
    """The one failure every channel reports when no knowledge base is set."""
    return ExternalSourceError(INVALID_ARGUMENT, MISSING_REF_MESSAGE, origin=origin)


def selector_argument(spec: ExternalSourceSpec) -> str:
    """AKB's public selector cannot be overridden by source configuration."""
    if spec.kind in {"akb-mcp", "akb-rest"}:
        return ID_SELECTOR_ARGUMENT
    return (spec.selector_argument or "").strip() or DEFAULT_SELECTOR_ARGUMENT


def base_argument(spec: ExternalSourceSpec) -> dict[str, Any]:
    """Resolve the canonical ID without numeric coercion or alias fallback."""
    value = (spec.ref or "").strip()
    if not value:
        raise missing_ref(spec.kind)
    if not re.fullmatch(r"KB_[A-Za-z0-9]{1,125}", value):
        raise ExternalSourceError(
            INVALID_ARGUMENT,
            "knowledgeBaseId must be a generated KB_ identifier; select a knowledge base",
            origin=spec.kind,
        )
    return {ID_SELECTOR_ARGUMENT: value}


# A tool call carries whatever the server's schema asks for, but these values are
# replayed on every retrieval, so they stay small, flat and scalar — no nested
# documents and no operator-supplied credentials riding along unnoticed.
MAX_TOOL_ARGUMENTS = 20
MAX_TOOL_ARGUMENT_VALUE_CHARS = 512
_TOOL_ARGUMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def sanitize_tool_arguments(raw: Any) -> dict[str, Any]:
    """Keep only arguments a hand-filled tool call can safely carry.

    The edit form lets an operator set extra arguments for the tool they picked;
    anything the schema would not accept is dropped rather than stored, so a
    retrieval years later cannot be steered by a value nobody reviewed.
    """
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, value in list(raw.items())[:MAX_TOOL_ARGUMENTS]:
        name = str(key).strip()
        if not _TOOL_ARGUMENT_KEY.fullmatch(name):
            continue
        # These are replayed verbatim, so anything beyond a scalar is dropped.
        if isinstance(value, (bool, int)):
            clean[name] = value
            continue
        if isinstance(value, float):
            # NaN and infinity have no JSON representation a server accepts.
            if not math.isfinite(value):
                continue
            clean[name] = value
            continue
        if isinstance(value, str) and len(value) <= MAX_TOOL_ARGUMENT_VALUE_CHARS:
            clean[name] = value
    return clean
