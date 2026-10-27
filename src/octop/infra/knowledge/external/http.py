"""Hardened outbound HTTP helpers for external knowledge sources."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from octop.infra.knowledge.external.types import (
    MAX_RESPONSE_BYTES,
    ExternalSourceError,
)


def validate_endpoint(url: str, *, allow_insecure_http: bool = False) -> str:
    """Validate a user-configured source endpoint.

    Credentials, query strings and fragments are rejected so a key can never be
    smuggled into the URL, and redirects are never followed by the callers.
    """
    text = (url or "").strip()
    if not text:
        raise ExternalSourceError("INVALID_ARGUMENT", "endpoint is required")
    parsed = urlsplit(text)
    if parsed.scheme not in ("http", "https"):
        raise ExternalSourceError("INVALID_ARGUMENT", "endpoint must be http or https")
    if parsed.scheme == "http" and not allow_insecure_http:
        raise ExternalSourceError(
            "INVALID_ARGUMENT", "http endpoints require the insecure-http option"
        )
    if parsed.username is not None or parsed.password is not None:
        raise ExternalSourceError("INVALID_ARGUMENT", "endpoint must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ExternalSourceError(
            "INVALID_ARGUMENT", "endpoint must not contain a query string or fragment"
        )
    if not (parsed.hostname or "").strip():
        raise ExternalSourceError("INVALID_ARGUMENT", "endpoint missing hostname")
    return text.rstrip("/")


async def post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
    origin: str,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> Any:
    """POST JSON and decode the response, mapping every failure to a sanitized error."""
    if not 0 < timeout_seconds <= 60:
        raise ExternalSourceError(
            "INVALID_ARGUMENT", "timeout must be between 0 and 60 seconds", origin=origin
        )
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise ExternalSourceError("TIMEOUT", "upstream request timed out", origin=origin) from exc
    except httpx.HTTPError as exc:
        raise ExternalSourceError(
            "TRANSPORT_ERROR", "upstream request failed", origin=origin
        ) from exc
    if response.status_code >= 500:
        raise ExternalSourceError(
            "UPSTREAM_ERROR", f"upstream returned HTTP {response.status_code}", origin=origin
        )
    if response.status_code >= 400:
        raise ExternalSourceError(
            "UPSTREAM_REJECTED", f"upstream returned HTTP {response.status_code}", origin=origin
        )
    raw = response.content[: max_bytes + 1]
    if len(raw) > max_bytes:
        raise ExternalSourceError(
            "RESPONSE_TOO_LARGE", "upstream response exceeds size limit", origin=origin
        )
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ExternalSourceError(
            "INVALID_JSON", "upstream returned invalid JSON", origin=origin
        ) from exc
