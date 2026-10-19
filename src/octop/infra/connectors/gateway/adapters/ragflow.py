"""Read-only RAGFlow retrieval gateway adapter."""

from __future__ import annotations

import json
from typing import Any

import httpx

from octop.infra.connectors.builder import normalize_ragflow_base_url

_MAX_RESULTS = 8
_MAX_CHUNK_CHARS = 1200

TOOLS: list[dict[str, Any]] = [
    {
        "name": "retrieve",
        "description": "Search the RAGFlow dataset approved for this virtual worker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Question to retrieve evidence for"},
                "dataset_id": {
                    "type": "string",
                    "description": "Dataset selector is fixed by the worker's approved source binding",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_RESULTS,
                    "description": f"Maximum passages (1-{_MAX_RESULTS})",
                },
            },
            "required": ["question"],
        },
    }
]


def list_tools() -> list[dict[str, Any]]:
    return TOOLS


def _request(
    creds: dict[str, Any], method: str, path: str, *, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    base_url = normalize_ragflow_base_url(str(creds.get("base_url") or ""))
    api_key = str(creds.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("RAGFlow API key is missing")
    try:
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            response = client.request(
                method,
                f"{base_url}{path}",
                headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
                json=body,
            )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"RAGFlow request failed: HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("RAGFlow request failed"):
            raise
        raise ValueError(f"RAGFlow request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("RAGFlow returned an invalid JSON response")
    if payload.get("code", 0) != 0:
        raise ValueError(f"RAGFlow request failed: {payload.get('message') or 'request rejected'}")
    return payload


def _retrieve(creds: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question") or "").strip()
    dataset_id = str(args.get("dataset_id") or "").strip()
    if not question:
        raise ValueError("question is required")
    if not dataset_id:
        raise ValueError("an approved dataset_id is required")
    top_k = max(1, min(int(args.get("top_k") or _MAX_RESULTS), _MAX_RESULTS))
    payload = _request(
        creds,
        "POST",
        "/retrieval",
        body={"question": question, "dataset_ids": [dataset_id], "top_k": top_k},
    )
    data = payload.get("data")
    chunks = data.get("chunks") if isinstance(data, dict) else []
    passages = []
    for chunk in chunks if isinstance(chunks, list) else []:
        if not isinstance(chunk, dict):
            continue
        content = str(chunk.get("content") or "")
        clipped = content[:_MAX_CHUNK_CHARS]
        passages.append(
            {
                "content": clipped,
                "dataset_id": chunk.get("dataset_id"),
                "document_id": chunk.get("document_id"),
                "document_name": chunk.get("document_keyword"),
                "similarity": chunk.get("similarity"),
                "truncated": len(content) > len(clipped),
            }
        )
    return {"question": question, "dataset_id": dataset_id, "passages": passages[:top_k]}


def call_tool(creds: dict[str, Any], name: str, args: dict[str, Any]) -> str:
    if name != "retrieve":
        raise ValueError(f"unknown tool: {name}")
    return json.dumps(_retrieve(creds, args), ensure_ascii=False)


def probe_credentials(creds: dict[str, Any]) -> None:
    _request(creds, "GET", "/datasets")
