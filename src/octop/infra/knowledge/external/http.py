"""Hardened outbound HTTP helpers for external knowledge sources."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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


def _decode(raw: bytes, *, max_bytes: int, origin: str) -> Any:
    if len(raw) > max_bytes:
        raise ExternalSourceError(
            "RESPONSE_TOO_LARGE", "upstream response exceeds size limit", origin=origin
        )
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        return None


def _error_code(
    body: Any,
    status: int,
    *,
    status_map: Mapping[int, str] | None,
    code_map: Mapping[str, str] | None,
) -> str:
    """Pick the caller-facing error code for a rejected upstream response.

    Upstreams that answer with a structured ``{"code": ...}`` body win over the
    HTTP status, so a 403 for "no scope" and a 404 for "unknown knowledge base"
    stay distinguishable — the API layer maps those onto distinct HTTP codes.
    """
    if isinstance(body, Mapping) and code_map:
        raw = body.get("code")
        if isinstance(raw, str) and raw.strip().upper() in code_map:
            return code_map[raw.strip().upper()]
    if status_map and status in status_map:
        return status_map[status]
    if status >= 500:
        return "UPSTREAM_ERROR"
    return "UPSTREAM_REJECTED"


def drop_api_prefix(url: str, prefix: str) -> str:
    """Remove an adapter-owned API prefix that was pasted into the endpoint.

    Adapters append their own path to the configured endpoint, so an endpoint
    holding the full retrieval URL yields a doubled path
    (``/api/v1/external/kb/retrievals/api/v1/external/kb/retrievals``) and a
    404 no amount of debugging on the upstream explains. Only that exact
    prefix is dropped: a gateway prefix stays, so proxied deployments keep
    working.
    """
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if path == prefix or path.startswith(prefix + "/"):
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    return url.rstrip("/")


def _public_target(url: str) -> str:
    """Scheme, host and path of an outbound request — never query, body or headers.

    A bare "upstream returned HTTP 404" cannot be acted on: surfacing the
    target lets an operator spot both mistakes at once — an endpoint that
    already carries a prefix (``/api/v1/external/api/v1/external/...``) and an
    endpoint pointing at the wrong service entirely.
    """
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"


async def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    timeout_seconds: float,
    origin: str,
    max_bytes: int,
    status_map: Mapping[int, str] | None,
    code_map: Mapping[str, str] | None,
) -> Any:
    if not 0 < timeout_seconds <= 60:
        raise ExternalSourceError(
            "INVALID_ARGUMENT", "timeout must be between 0 and 60 seconds", origin=origin
        )
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            response = await client.request(
                method, url, headers=headers, json=payload if method == "POST" else None
            )
    except httpx.TimeoutException as exc:
        raise ExternalSourceError("TIMEOUT", "upstream request timed out", origin=origin) from exc
    except httpx.HTTPError as exc:
        raise ExternalSourceError(
            "TRANSPORT_ERROR", "upstream request failed", origin=origin
        ) from exc
    raw = response.content[: max_bytes + 1]
    body = _decode(raw, max_bytes=max_bytes, origin=origin)
    if response.status_code >= 400:
        # The status is safe to surface; upstream messages are not.
        raise ExternalSourceError(
            _error_code(body, response.status_code, status_map=status_map, code_map=code_map),
            f"upstream returned HTTP {response.status_code} at {_public_target(url)}",
            origin=origin,
        )
    if body is None:
        raise ExternalSourceError("INVALID_JSON", "upstream returned invalid JSON", origin=origin)
    return body


async def post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
    origin: str,
    max_bytes: int = MAX_RESPONSE_BYTES,
    status_map: Mapping[int, str] | None = None,
    code_map: Mapping[str, str] | None = None,
) -> Any:
    """POST JSON and decode the response, mapping every failure to a sanitized error."""
    return await _request(
        "POST",
        url,
        headers=headers,
        payload=payload,
        timeout_seconds=timeout_seconds,
        origin=origin,
        max_bytes=max_bytes,
        status_map=status_map,
        code_map=code_map,
    )


async def get_json(
    url: str,
    *,
    headers: dict[str, str],
    timeout_seconds: float,
    origin: str,
    max_bytes: int = MAX_RESPONSE_BYTES,
    status_map: Mapping[int, str] | None = None,
    code_map: Mapping[str, str] | None = None,
) -> Any:
    """GET JSON and decode the response, mapping every failure to a sanitized error."""
    return await _request(
        "GET",
        url,
        headers=headers,
        payload=None,
        timeout_seconds=timeout_seconds,
        origin=origin,
        max_bytes=max_bytes,
        status_map=status_map,
        code_map=code_map,
    )
