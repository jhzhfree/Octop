"""Explicitly scoped, read-only client for AKB's versioned external API.

This adapter is intentionally not an automatic Agent tool: the caller must
authorize the worker, API key and knowledge base before invoking it.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class AkbError(RuntimeError):
    """Sanitized upstream error; never includes credentials or response bodies."""


@dataclass(frozen=True)
class AkbConfig:
    base_url: str
    api_key: str
    knowledge_base_id: int
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("AKB endpoint must be an HTTPS origin without credentials")
        if not self.api_key.strip():
            raise ValueError("AKB API key is required")
        if type(self.knowledge_base_id) is not int or self.knowledge_base_id <= 0:
            raise ValueError("AKB knowledge base ID must be a positive integer")
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("AKB timeout must be between 0 and 60 seconds")


class AkbClient:
    def __init__(self, config: AkbConfig) -> None:
        self.config = config

    def _request(self, endpoint: str, question: str, top_k: int, tags: list[str]) -> dict:
        payload = json.dumps(
            {
                "knowledgeBaseId": self.config.knowledge_base_id,
                "question": question,
                "topK": top_k,
                "permissionTags": tags,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self.config.base_url.rstrip("/") + "/api/v1/external/" + endpoint,
            data=payload,
            headers={
                "X-API-Key": self.config.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            # Do not follow redirects: credentials must never be forwarded.
            from urllib.request import build_opener, HTTPRedirectHandler

            class NoRedirect(HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            with build_opener(NoRedirect()).open(
                request, timeout=self.config.timeout_seconds
            ) as response:
                raw = response.read(2_000_001)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise AkbError("AKB request failed") from None
        if len(raw) > 2_000_000:
            raise AkbError("AKB response exceeds size limit")
        try:
            envelope = json.loads(raw)
        except (ValueError, UnicodeError):
            raise AkbError("AKB returned invalid JSON") from None
        if not isinstance(envelope, dict) or envelope.get("success") is not True:
            raise AkbError("AKB returned an unsuccessful response")
        if not isinstance(envelope.get("data"), (dict, list)):
            raise AkbError("AKB response data is invalid")
        return {
            "data": envelope["data"],
            "trace_id": envelope.get("traceId"),
            "knowledge_base_id": self.config.knowledge_base_id,
        }

    async def search(
        self, question: str, *, top_k: int = 10, permission_tags: list[str] | None = None
    ) -> dict:
        if not question.strip() or len(question) > 4000:
            raise ValueError("question must contain 1-4000 characters")
        if type(top_k) is not int or not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        tags = permission_tags or []
        if not isinstance(tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in tags
        ):
            raise ValueError("permission tags must be nonempty strings")
        return await asyncio.to_thread(self._request, "search", question, top_k, tags)

    async def query(
        self, question: str, *, top_k: int = 10, permission_tags: list[str] | None = None
    ) -> dict:
        if not question.strip() or len(question) > 4000:
            raise ValueError("question must contain 1-4000 characters")
        if type(top_k) is not int or not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        tags = permission_tags or []
        if not isinstance(tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in tags
        ):
            raise ValueError("permission tags must be nonempty strings")
        return await asyncio.to_thread(self._request, "query", question, top_k, tags)
