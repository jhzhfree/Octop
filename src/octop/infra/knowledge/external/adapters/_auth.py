"""How an AKB API key is presented on the wire.

AKB itself expects ``X-API-Key``, but deployments behind a gateway that strips
unknown headers only accept ``Authorization: Bearer``. The scheme therefore
travels with the credentials instead of being hardcoded per adapter.
"""

from __future__ import annotations

from typing import Any

from octop.infra.knowledge.external.types import API_KEY_HEADERS, ExternalSourceError

__all__ = ["auth_headers"]


def auth_headers(creds: dict[str, Any], *, origin: str) -> dict[str, str]:
    api_key = str(creds.get("api_key") or "").strip()
    if not api_key:
        raise ExternalSourceError("INVALID_ARGUMENT", "AKB API key is missing", origin=origin)
    scheme = str(creds.get("api_key_header") or "").strip().lower() or API_KEY_HEADERS[0]
    if scheme == "authorization":
        return {"Authorization": f"Bearer {api_key}"}
    return {"X-API-Key": api_key}
