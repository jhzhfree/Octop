"""Per-invocation exact-byte signing shared by all AKB transports."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_CONTEXT: ContextVar[tuple[dict[str, Any], rsa.RSAPrivateKey, str, str, dict[str, str]] | None] = (
    ContextVar("akb_request_auth", default=None)
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def public_jwk(pem: str | rsa.RSAPrivateKey) -> dict[str, str]:
    key = (
        serialization.load_pem_private_key(pem.encode(), password=None)
        if isinstance(pem, str)
        else pem
    )
    if not isinstance(key, rsa.RSAPrivateKey) or not 3072 <= key.key_size <= 4096:
        raise ValueError("RSA 3072–4096 key required")
    numbers = key.public_key().public_numbers()
    if numbers.e != 65537:
        raise ValueError("unsupported RSA exponent")

    def encode(n: int) -> str:
        return (
            base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big"))
            .decode()
            .rstrip("=")
        )

    core = {"e": encode(numbers.e), "kty": "RSA", "n": encode(numbers.n)}
    kid = (
        base64.urlsafe_b64encode(
            hashlib.sha256(
                json.dumps(core, separators=(",", ":"), sort_keys=True).encode()
            ).digest()
        )
        .decode()
        .rstrip("=")
    )
    return {**core, "kid": kid, "alg": "RS256", "use": "sig"}


@contextmanager
def signed_requests(
    binding: dict[str, Any], pem: str, key: str, invocation: dict[str, str]
) -> Iterator[None]:
    private = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(private, rsa.RSAPrivateKey):
        raise ValueError("RSA private key required")
    token = _CONTEXT.set((binding, private, public_jwk(private)["kid"], key, invocation))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def request_headers(method: str, url: str, body: bytes) -> dict[str, str]:
    context = _CONTEXT.get()
    if context is None:
        return {}
    b, private, kid, credential, invocation = context
    parts = urlsplit(url)
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    now = int(time.time())
    claims = {
        "ver": 2,
        "iss": b["issuer"],
        "aud": b["audience"],
        "resource": b["resource"],
        "policy_id": str(b["policyId"]),
        "azp": str(b["applicationPrincipalId"]),
        "sub": "agent:" + b["externalAgentId"],
        "platform_id": b["platformId"],
        "installation_id": b["installationId"],
        "workspace_id": b["workspaceId"],
        "runtime_agent": b["runtimeAgentKey"],
        "session_id": invocation["sessionId"],
        "invocation_id": invocation["invocationId"],
        "iat": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
        "credential_sha256": _sha(credential.encode()),
        "request_binding": {"method": method, "target": target, "body_sha256": _sha(body)},
    }
    for source, destination in (
        ("terminalAgentName", "terminal_agent"),
        ("messageId", "message_id"),
    ):
        if source in invocation:
            claims[destination] = invocation[source]
    proof = jwt.encode(
        claims,
        private,
        algorithm="RS256",
        headers={"kid": kid, "typ": "akb-agent-proof+jwt"},
    )
    return {"X-API-Key": credential, "X-AKB-Workload-Assertion": proof}
