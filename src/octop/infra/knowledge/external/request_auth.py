"""Octop's async signing scope; cryptography and AKB wire format live in the SDK."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from akb_identity_sdk import HEADER, create_request_proof
from akb_identity_sdk import public_jwk as public_jwk
from akb_identity_sdk.crypto import private_key
from cryptography.hazmat.primitives.asymmetric import rsa

_CONTEXT: ContextVar[tuple[dict[str, Any], rsa.RSAPrivateKey, str, dict[str, str]] | None] = (
    ContextVar("akb_request_auth", default=None)
)


@contextmanager
def signed_requests(
    binding: dict[str, Any], pem: str, key: str, invocation: dict[str, str]
) -> Iterator[None]:
    token = _CONTEXT.set((dict(binding), private_key(pem), key, dict(invocation)))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def request_headers(method: str, url: str, body: bytes) -> dict[str, str]:
    context = _CONTEXT.get()
    if context is None:
        return {}
    binding, private, credential, invocation = context
    proof = create_request_proof(
        binding=binding,
        private_pem=private,
        service_key=credential,
        method=method,
        url=url,
        body=body,
        session_id=invocation["sessionId"],
        invocation_id=invocation["invocationId"],
        terminal_agent=invocation.get("terminalAgentName"),
        message_id=invocation.get("messageId"),
    )
    return {"X-API-Key": credential, HEADER: proof}
