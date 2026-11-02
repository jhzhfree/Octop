"""AKB AGENT_KEY_RS256_V2 request signing and independent public-key verification."""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from .crypto import generate_uuid, private_key, public_jwk, public_key, validate_uuid

PROFILE = "AGENT_KEY_RS256_V2"
HEADER = "X-AKB-Workload-Assertion"
TYPE = "akb-agent-proof+jwt"
_FIELDS = {
    "iss": "issuer",
    "aud": "audience",
    "resource": "resource",
    "policy_id": "policyId",
    "azp": "applicationPrincipalId",
    "platform_id": "platformId",
    "installation_id": "installationId",
    "workspace_id": "workspaceId",
    "runtime_agent": "runtimeAgentKey",
}


def origin(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
    ):
        raise ValueError("Absolute HTTP(S) URL without userinfo or fragment required")
    host = parts.hostname.encode("idna").decode().lower()
    host = "[" + host + "]" if ":" in host else host
    port = parts.port
    suffix = (
        ""
        if port is None or (parts.scheme, port) in {("http", 80), ("https", 443)}
        else ":" + str(port)
    )
    return parts.scheme + "://" + host + suffix


def validate_binding(
    binding: Mapping[str, Any], url: str, agent_id: str | None = None
) -> dict[str, str]:
    result = {field: str(binding[field]) for field in (*_FIELDS.values(), "externalAgentId")}
    if any(
        not value or len(value) > 2048 or any(ord(c) < 32 for c in value)
        for value in result.values()
    ):
        raise ValueError("Invalid AKB binding")
    validate_uuid(result["externalAgentId"])
    if agent_id is not None and result["externalAgentId"] != validate_uuid(agent_id):
        raise ValueError("AKB binding Agent UUID mismatch")
    if result["resource"] != origin(url):
        raise ValueError("AKB binding resource mismatch")
    return result


def _text(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Invalid " + label)
    return value


def request_binding(method: str, url: str, body: bytes) -> dict[str, str]:
    origin(url)
    if not isinstance(body, bytes):
        raise TypeError("Request body must be the exact bytes sent")
    if not isinstance(method, str) or method != method.upper() or not method.isalpha():
        raise ValueError("Uppercase HTTP method required")
    parts = urlsplit(url)
    target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    return {"method": method, "target": target, "body_sha256": hashlib.sha256(body).hexdigest()}


def create_request_proof(
    *,
    binding: Mapping[str, Any],
    private_pem: str | rsa.RSAPrivateKey,
    service_key: str,
    method: str,
    url: str,
    body: bytes,
    session_id: str,
    invocation_id: str,
    terminal_agent: str | None = None,
    message_id: str | None = None,
    ttl: int = 60,
    now: int | None = None,
) -> str:
    if type(ttl) is not int or not 1 <= ttl <= 60:
        raise ValueError("Proof TTL must be 1..60 seconds")
    b = validate_binding(binding, url)
    key = private_key(private_pem)
    issued = int(time.time()) if now is None else now
    claims: dict[str, Any] = {name: b[field] for name, field in _FIELDS.items()}
    claims.update(
        {
            "ver": 2,
            "sub": "agent:" + b["externalAgentId"],
            "session_id": _text(session_id, "session ID"),
            "invocation_id": _text(invocation_id, "invocation ID"),
            "iat": issued,
            "exp": issued + ttl,
            "jti": generate_uuid(),
            "credential_sha256": hashlib.sha256(
                _text(service_key, "service Key").encode()
            ).hexdigest(),
            "request_binding": request_binding(method, url, body),
        }
    )
    if terminal_agent is not None:
        claims["terminal_agent"] = _text(terminal_agent, "terminal Agent")
    if message_id is not None:
        claims["message_id"] = _text(message_id, "message ID")
    return jwt.encode(
        claims, key, algorithm="RS256", headers={"kid": public_jwk(key)["kid"], "typ": TYPE}
    )


class ReplayStore(Protocol):
    """Shared stores must implement an atomic insert-if-absent with expiry."""

    def claim(self, nonce: str, expires_at: float) -> bool: ...


class MemoryReplayStore:
    """Thread-safe single-process store. Use a shared implementation across server replicas."""

    def __init__(self, capacity: int = 10000):
        if capacity < 1:
            raise ValueError("Positive replay capacity required")
        self._capacity = capacity
        self._entries: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, nonce: str, expires_at: float) -> bool:
        with self._lock:
            now = time.time()
            self._entries = {key: expiry for key, expiry in self._entries.items() if expiry > now}
            if expires_at <= now or nonce in self._entries or len(self._entries) >= self._capacity:
                return False
            self._entries[nonce] = expires_at
            return True


def verify_request_proof(
    proof: str,
    *,
    public_material: str | dict[str, Any] | rsa.RSAPublicKey,
    binding: Mapping[str, Any],
    service_key: str,
    method: str,
    url: str,
    body: bytes,
    replay_store: ReplayStore,
    clock_skew: int = 0,
) -> dict[str, Any]:
    """Verify against a trusted, already-registered binding and public key, never token metadata.

    The host still checks key status/revocation, service scopes and resource grants.
    Raises ValueError or jwt.InvalidTokenError on failure. No unchecked decode fallback.
    """
    if not isinstance(proof, str) or len(proof) > 16384 or not 0 <= clock_skew <= 30:
        raise ValueError("Invalid proof or clock skew")
    b = validate_binding(binding, url)
    key = public_key(public_material)
    kid = public_jwk(key)["kid"]
    header = jwt.get_unverified_header(proof)
    if header.get("alg") != "RS256" or header.get("typ") != TYPE or header.get("kid") != kid:
        raise ValueError("Proof profile or registered key mismatch")
    required = [
        *_FIELDS,
        "sub",
        "ver",
        "session_id",
        "invocation_id",
        "iat",
        "exp",
        "jti",
        "credential_sha256",
        "request_binding",
    ]
    claims = jwt.decode(
        proof,
        key,
        algorithms=["RS256"],
        audience=b["audience"],
        issuer=b["issuer"],
        leeway=clock_skew,
        options={"require": required},
    )
    if type(claims["ver"]) is not int or claims["ver"] != 2:
        raise ValueError("V2 proof required")
    if (
        type(claims["iat"]) is not int
        or type(claims["exp"]) is not int
        or not 1 <= claims["exp"] - claims["iat"] <= 60
    ):
        raise ValueError("Invalid proof lifetime")
    for claim, field in _FIELDS.items():
        if claims[claim] != b[field]:
            raise ValueError("Registered binding mismatch: " + claim)
    if claims["sub"] != "agent:" + b["externalAgentId"]:
        raise ValueError("Agent UUID mismatch")
    _text(claims["session_id"], "session ID")
    _text(claims["invocation_id"], "invocation ID")
    validate_uuid(claims["jti"])
    if not hmac.compare_digest(
        str(claims["credential_sha256"]),
        hashlib.sha256(_text(service_key, "service Key").encode()).hexdigest(),
    ):
        raise ValueError("Service credential mismatch")
    if claims["request_binding"] != request_binding(method, url, body):
        raise ValueError("Exact request binding mismatch")
    nonce = hashlib.sha256((b["issuer"] + "\0" + kid + "\0" + claims["jti"]).encode()).hexdigest()
    if not replay_store.claim(nonce, claims["exp"] + clock_skew):
        raise ValueError("Proof replay or replay store full")
    return claims
