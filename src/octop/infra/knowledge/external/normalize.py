"""Normalize heterogeneous upstream payloads into :class:`ExternalChunk`."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from octop.infra.knowledge.external.types import MAX_CHUNK_CHARS, ExternalChunk

_CONTENT_KEYS = ("content", "text", "passage", "chunk", "snippet", "chunkText")
_ID_KEYS = ("id", "chunk_id", "chunkId", "evidenceCode", "evidence_code", "docId", "doc_id")
_SOURCE_KEYS = (
    "source",
    "documentName",
    "document_name",
    "documentKeyword",
    "document_keyword",
    "sourceUri",
    "source_uri",
    "docName",
    "doc_name",
    "title",
    "url",
)
_SCORE_KEYS = ("score", "similarity", "relevance", "rerankScore", "rerank_score")
_DISTANCE_KEYS = ("distance", "scoreDistance", "score_distance")


def _first_str(raw: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def coerce_score(value: Any) -> float:
    """Map an upstream relevance value onto ``[0, 1]`` where higher is better."""
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return 0.0
    if not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if number != number:  # NaN
        return 0.0
    # Percentage-style scales (0-100) are common; fold them into 0-1.
    if number > 1.0:
        number = number / 100.0 if number <= 100.0 else 1.0
    return max(0.0, min(1.0, number))


def _score_from(raw: Mapping[str, Any]) -> float:
    for key in _SCORE_KEYS:
        if key in raw:
            return coerce_score(raw[key])
    for key in _DISTANCE_KEYS:
        if key in raw:
            # Distance is inverse to relevance.
            return max(0.0, min(1.0, 1.0 - coerce_score(raw[key])))
    return 0.0


def _stable_id(raw: Mapping[str, Any], content: str, index: int) -> str:
    explicit = _first_str(raw, _ID_KEYS)
    if explicit:
        return explicit
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"chunk-{digest}-{index}"


def chunk_from_mapping(
    raw: Mapping[str, Any],
    *,
    origin: str,
    ref: str = "",
    index: int = 0,
    max_chars: int = MAX_CHUNK_CHARS,
) -> ExternalChunk | None:
    """Convert one upstream record; ``None`` when it carries no usable text."""
    content = _first_str(raw, _CONTENT_KEYS)
    if not content:
        return None
    clipped = content[:max_chars]
    metadata: dict[str, Any] = {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and key not in _CONTENT_KEYS
    }
    if len(content) > len(clipped):
        metadata["truncated"] = True
    return ExternalChunk(
        id=_stable_id(raw, clipped, index),
        content=clipped,
        score=_score_from(raw),
        source=_first_str(raw, _SOURCE_KEYS),
        metadata=metadata,
        origin=origin,
        ref=ref,
    )


def normalize_chunks(
    items: Sequence[Any],
    *,
    origin: str,
    ref: str = "",
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[ExternalChunk]:
    """Convert an upstream list, dropping unusable records."""
    chunks: list[ExternalChunk] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        chunk = chunk_from_mapping(item, origin=origin, ref=ref, index=index, max_chars=max_chars)
        if chunk is not None:
            chunks.append(chunk)
    return chunks


def extract_items(payload: Any, *, keys: Sequence[str] = ()) -> list[Any]:
    """Find the passage array inside an unknown upstream envelope."""
    candidates = list(keys) + [
        "chunks",
        "results",
        "items",
        "passages",
        "data",
        "candidates",
        "matches",
    ]
    if isinstance(payload, list):
        return list(payload)
    if not isinstance(payload, Mapping):
        return []
    for key in candidates:
        value = payload.get(key)
        if isinstance(value, list):
            return list(value)
        if isinstance(value, Mapping):
            nested = extract_items(value, keys=keys)
            if nested:
                return nested
    return []


def dedupe_chunks(chunks: Sequence[ExternalChunk]) -> list[ExternalChunk]:
    """Drop repeats across sources, keeping the highest-scoring copy."""
    best: dict[str, ExternalChunk] = {}
    for chunk in chunks:
        key = chunk.dedupe_key()
        existing = best.get(key)
        if existing is None or chunk.score > existing.score:
            best[key] = chunk
    return list(best.values())


def filter_chunks(
    chunks: Sequence[ExternalChunk], *, min_score: float = 0.0, limit: int = 0
) -> list[ExternalChunk]:
    """Apply the per-source score floor and result cap (score-ordered)."""
    kept = [chunk for chunk in chunks if chunk.score >= min_score]
    kept.sort(key=lambda chunk: chunk.score, reverse=True)
    if limit > 0:
        return kept[:limit]
    return kept
