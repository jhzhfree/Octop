"""Machine-readable knowledge citation payload embedded in tool results."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, NotRequired, TypedDict

CITATIONS_MARKER_PREFIX = "<!--octop-kb-citations:"
CITATIONS_MARKER_SUFFIX = "-->"


class KnowledgeCitation(TypedDict):
    kb_id: str
    kb_name: str
    doc_id: str
    filename: str
    path: str
    source_id: NotRequired[str]
    url: NotRequired[str]


def citations_from_ranked(ranked: Sequence[tuple[Any, Any, Any]]) -> list[KnowledgeCitation]:
    """Deduplicate ranked hits to one citation per document (retrieval order)."""
    seen: set[str] = set()
    out: list[KnowledgeCitation] = []
    for base, _hit, document in ranked:
        doc_id = str(getattr(document, "id", "") or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(
            {
                "kb_id": str(getattr(base, "id", "") or ""),
                "kb_name": str(getattr(base, "name", "") or ""),
                "doc_id": doc_id,
                "filename": str(getattr(document, "filename", "") or doc_id),
                "path": str(getattr(document, "path", "") or ""),
            }
        )
    return out


def citations_from_external(chunks: Sequence[Any]) -> list[KnowledgeCitation]:
    from urllib.parse import urlsplit

    seen: set[str] = set()
    result: list[KnowledgeCitation] = []
    for chunk in chunks:
        if not chunk.source_id:
            continue
        document = str(
            chunk.metadata.get("documentId")
            or chunk.metadata.get("docId")
            or chunk.source
            or chunk.id
        )
        identifier = f"{chunk.source_id}:{chunk.ref}:{document}"
        if identifier in seen:
            continue
        seen.add(identifier)
        raw_url = str(chunk.metadata.get("url") or chunk.metadata.get("sourceUrl") or "")
        parts = urlsplit(raw_url)
        url = (
            raw_url
            if parts.scheme in {"http", "https"}
            and parts.hostname
            and not parts.username
            and not parts.password
            else ""
        )
        result.append(
            {
                "kb_id": chunk.ref,
                "kb_name": chunk.ref,
                "doc_id": identifier,
                "filename": chunk.source or document,
                "path": "",
                "source_id": chunk.source_id,
                "url": url,
            }
        )
    return result


_CITATIONS_MARKER_RE = re.compile(
    r"\n*\s*"
    + re.escape(CITATIONS_MARKER_PREFIX)
    + r"\[.*?\]"
    + re.escape(CITATIONS_MARKER_SUFFIX)
    + r"\s*$",
    re.DOTALL,
)


def append_citations_marker(text: str, citations: Sequence[KnowledgeCitation]) -> str:
    """Append a HTML-comment marker the dashboard can parse; LLM usually ignores it."""
    body = (text or "").rstrip()
    if not body or not citations:
        return body
    payload = json.dumps(list(citations), ensure_ascii=False, separators=(",", ":"))
    return f"{body}\n\n{CITATIONS_MARKER_PREFIX}{payload}{CITATIONS_MARKER_SUFFIX}"


def strip_citations_marker(text: str) -> str:
    """Remove the dashboard citation marker from tool output (for model context)."""
    if not text or CITATIONS_MARKER_PREFIX not in text:
        return text
    return _CITATIONS_MARKER_RE.sub("", text).rstrip()
