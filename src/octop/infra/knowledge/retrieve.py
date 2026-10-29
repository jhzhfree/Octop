"""Retrieve readable knowledge-base chunks for one chat turn."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from octop.i18n import tr
from octop.infra.knowledge.citations import append_citations_marker, citations_from_ranked
from octop.infra.knowledge.embed import embed_knowledge_texts
from octop.infra.knowledge.external.fuse import rrf_fuse
from octop.infra.knowledge.external.service import ExternalKnowledgeService
from octop.infra.knowledge.external.types import ExternalChunk, ExternalSourceSpec
from octop.infra.knowledge.gate import assert_knowledge_usable
from octop.infra.knowledge.index import Hit, KnowledgeIndex

DEFAULT_RETRIEVAL_K = 8
DEFAULT_CONTEXT_CHAR_BUDGET = 6000

logger = logging.getLogger(__name__)


async def retrieve_context(
    services: Any,
    *,
    user_id: int,
    is_admin: bool,
    query: str | None,
    knowledge_base_ids: Sequence[str],
    k: int = DEFAULT_RETRIEVAL_K,
    char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET,
    locale: str = "en",
    visible_bases: Sequence[Any] | None = None,
    external_specs: Sequence[ExternalSourceSpec] | None = None,
) -> str:
    """Return formatted readable context, skipping unavailable or failed retrieval.

    When ``external_specs`` is present, local hits and external passages are
    merged by reciprocal-rank fusion because their scores use different scales.
    """
    if not (query or "").strip() or k <= 0 or char_budget <= 0:
        return ""
    if not knowledge_base_ids and not external_specs:
        return ""
    loop = asyncio.get_running_loop()
    ranked: list[tuple[Any, Hit, Any]] = []
    if knowledge_base_ids:
        try:
            ranked = await loop.run_in_executor(
                None,
                lambda: _rank_local(
                    services,
                    user_id=user_id,
                    is_admin=is_admin,
                    query=query,
                    knowledge_base_ids=knowledge_base_ids,
                    k=k,
                    visible_bases=visible_bases,
                ),
            )
        except Exception:
            logger.warning("knowledge retrieval skipped for user=%s", user_id, exc_info=True)
            ranked = []
    external_chunks = await _external_chunks(services, external_specs, query, k=k)
    if not external_chunks:
        if not ranked:
            return ""
        return _format_context(ranked[:k], char_budget=char_budget, locale=locale)
    local_chunks = [_chunk_for_hit(item) for item in ranked]
    fused = rrf_fuse(
        [local_chunks, external_chunks],
        limit=k,
        key=lambda chunk: f"{chunk.origin}:{chunk.dedupe_key()}",
    )
    return _format_fused(fused, ranked, char_budget=char_budget, locale=locale)


async def _external_chunks(
    services: Any,
    specs: Sequence[ExternalSourceSpec] | None,
    query: str | None,
    *,
    k: int,
) -> list[ExternalChunk]:
    """Retrieve from external sources; a failing source degrades to empty."""
    if not specs:
        return []
    try:
        result = await _external_service(services).retrieve(
            query=(query or "").strip(), specs=list(specs), limit=k
        )
    except Exception:
        logger.warning("external knowledge retrieval failed", exc_info=True)
        return []
    chunks: list[ExternalChunk] = []
    for item in result.get("chunks", []):
        if not isinstance(item, dict):
            continue
        chunks.append(
            ExternalChunk(
                id=str(item.get("id") or ""),
                content=str(item.get("content") or ""),
                score=float(item.get("score") or 0.0),
                source=str(item.get("source") or ""),
                metadata=(item["metadata"] if isinstance(item.get("metadata"), dict) else {}),
                origin=str(item.get("origin") or ""),
                ref=str(item.get("ref") or ""),
            )
        )
    return chunks


def _external_service(services: Any) -> ExternalKnowledgeService:
    return ExternalKnowledgeService(
        services.external_knowledge_repo,
        services.connector_repo,
        services.secret_repo,
        services.config,
    )


def external_specs_for_targets(
    services: Any, target_type: str, target_ids: Sequence[str]
) -> list[ExternalSourceSpec]:
    """Enabled external sources bound to the given agents or knowledge bases."""
    try:
        return _external_service(services).specs_for_targets(
            target_type, [str(target_id) for target_id in target_ids]
        )
    except Exception:
        logger.warning("external knowledge binding lookup failed", exc_info=True)
        return []


def _chunk_for_hit(item: tuple[Any, Hit, Any]) -> ExternalChunk:
    base, hit, document = item
    return ExternalChunk(
        id=f"local:{hit.doc_id}:{hit.ordinal}",
        content=hit.text.strip(),
        score=float(hit.score),
        source=str(getattr(base, "name", "") or ""),
        metadata={
            "kb_name": str(getattr(base, "name", "") or ""),
            "filename": str(getattr(document, "filename", "") or ""),
            "ordinal": int(hit.ordinal),
        },
        origin="local",
    )


def _rank_local(
    services: Any,
    *,
    user_id: int,
    is_admin: bool,
    query: str | None,
    knowledge_base_ids: Sequence[str],
    k: int,
    visible_bases: Sequence[Any] | None,
) -> list[tuple[Any, Hit, Any]]:
    assert_knowledge_usable(services.settings_repo.get, getattr(services, "provider_repo", None))
    query_vectors = embed_knowledge_texts(services, [(query or "").strip()])
    if not query_vectors:
        return []

    visible = visible_bases
    if visible is None:
        visible = (
            services.knowledge_repo.list_all()
            if is_admin
            else services.knowledge_repo.list_visible(user_id)
        )
    visible_by_id = {base.id: base for base in visible}
    selected_ids = _unique_ids(knowledge_base_ids)

    ranked: list[tuple[Any, Hit, Any]] = []
    for kb_id in selected_ids:
        base = visible_by_id.get(kb_id)
        if base is None:
            continue
        ready_documents = {
            document.id: document
            for document in services.knowledge_repo.list_documents(kb_id)
            if document.status == "ready" and not document.is_dir
        }
        for hit in KnowledgeIndex(kb_id).search(query_vectors[0], k=k):
            document = ready_documents.get(hit.doc_id)
            if document is not None:
                ranked.append((base, hit, document))

    ranked.sort(key=lambda item: item[1].score, reverse=True)
    return ranked[:k]


def _format_fused(
    chunks: Sequence[ExternalChunk],
    ranked: Sequence[tuple[Any, Hit, Any]],
    *,
    char_budget: int,
    locale: str,
) -> str:
    """Render fused local + external passages under one shared budget."""
    sections: list[str] = []
    remaining = char_budget
    for chunk in chunks:
        if chunk.origin == "local":
            citation = tr(
                "knowledge.retrieval.citation",
                locale,
                name=chunk.metadata.get("kb_name", ""),
                filename=chunk.metadata.get("filename", ""),
                ordinal=int(chunk.metadata.get("ordinal", 0)) + 1,
            )
        else:
            citation = tr(
                "knowledge.retrieval.external_citation",
                locale,
                origin=chunk.origin,
                source=chunk.source or chunk.ref,
            )
        if remaining <= len(citation):
            break
        text = chunk.content.strip()[: remaining - len(citation)]
        if not text:
            continue
        section = f"{citation}{text}"
        sections.append(section)
        remaining -= len(section)
    if not sections:
        return ""
    body = tr("knowledge.retrieval.preamble", locale) + "\n\n" + "\n\n".join(sections)
    return append_citations_marker(body, citations_from_ranked(ranked))


def _unique_ids(ids: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(kb_id).strip() for kb_id in ids if str(kb_id).strip()))


def _format_context(
    ranked: Sequence[tuple[Any, Hit, Any]], *, char_budget: int, locale: str
) -> str:
    sections: list[str] = []
    remaining = char_budget
    for base, hit, document in ranked:
        citation = tr(
            "knowledge.retrieval.citation",
            locale,
            name=base.name,
            filename=document.filename,
            ordinal=hit.ordinal + 1,
        )
        if remaining <= len(citation):
            break
        text = hit.text.strip()[: remaining - len(citation)]
        if not text:
            continue
        section = f"{citation}{text}"
        sections.append(section)
        remaining -= len(section)
    if not sections:
        return ""
    body = tr("knowledge.retrieval.preamble", locale) + "\n\n" + "\n\n".join(sections)
    return append_citations_marker(body, citations_from_ranked(ranked))
