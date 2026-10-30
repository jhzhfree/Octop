"""Short-lived Octop assertions for AKB delegated-user requests.

AKB verifies these assertions against the JWKS this instance publishes, so the
signing key and the published key set are derived from the same material here:
rotating ``OCTOP_AKB_IDENTITY_PRIVATE_KEY`` rotates both at once.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@dataclass(frozen=True)
class OctopIdentity:
    """Trusted identity taken from Octop's authenticated runtime context."""

    user_id: str
    agent_id: str
    trace_id: str = ""
    workload_only: bool = False


def delegated_identity_enabled() -> bool:
    """Whether this Octop instance is provisioned to sign AKB assertions."""
    return os.environ.get("OCTOP_AKB_IDENTITY_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _int_to_bytes(value: int) -> bytes:
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def _load_signing_key() -> rsa.RSAPrivateKey | None:
    """The configured RSA signing key, or None when delegation is unprovisioned."""
    pem = os.environ.get("OCTOP_AKB_IDENTITY_PRIVATE_KEY", "").replace("\\n", "\n").strip()
    if not pem:
        return None
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("OCTOP_AKB_IDENTITY_PRIVATE_KEY must be an RSA private key")
    return key


def _thumbprint(modulus: str, exponent: str) -> str:
    """RFC 7638 thumbprint, so the key id is a property of the key itself."""
    canonical = json.dumps(
        {"e": exponent, "kty": "RSA", "n": modulus},
        separators=(",", ":"),
        sort_keys=True,
    )
    return _b64url(hashlib.sha256(canonical.encode("utf-8")).digest())


def public_jwk(key: rsa.RSAPrivateKey | None = None) -> dict[str, str]:
    """The public half of the signing key, in the shape AKB expects."""
    loaded = key if key is not None else _load_signing_key()
    if loaded is None:
        raise ValueError("AKB delegated identity signing is not fully configured")
    numbers = loaded.public_key().public_numbers()
    modulus = _b64url(_int_to_bytes(numbers.n))
    exponent = _b64url(_int_to_bytes(numbers.e))
    configured = os.environ.get("OCTOP_AKB_IDENTITY_KEY_ID", "").strip()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        # A configured key id wins so an operator can pin a stable name across
        # rotations; the thumbprint is the safe default because it changes
        # exactly when the key does.
        "kid": configured or _thumbprint(modulus, exponent),
        "n": modulus,
        "e": exponent,
    }


def jwks_document() -> dict[str, list[dict[str, str]]]:
    """Key set AKB fetches to verify ``X-Octop-Identity``.

    Empty when this instance has no signing key. The document holds nothing
    secret, but an unprovisioned instance publishes no keys instead of failing
    the fetch: AKB then rejects assertions with its own explicit config error.
    """
    key = _load_signing_key()
    if key is None:
        return {"keys": []}
    return {"keys": [public_jwk(key)]}


def issue_assertion(identity: OctopIdentity) -> str:
    """Sign the AKB `X-Octop-Identity` assertion with a one-minute lifetime."""
    issuer = os.environ.get("OCTOP_AKB_IDENTITY_ISSUER", "").strip()
    audience = os.environ.get("OCTOP_AKB_IDENTITY_AUDIENCE", "").strip()
    instance_id = os.environ.get("OCTOP_AKB_INSTANCE_ID", "").strip()
    key = _load_signing_key()
    if not all((issuer, audience, instance_id)) or key is None:
        raise ValueError("AKB delegated identity signing is not fully configured")
    user_id = str(identity.user_id).strip()
    agent_id = str(identity.agent_id).strip()
    if not user_id or len(user_id) > 255 or not agent_id or len(agent_id) > 255:
        raise ValueError("AKB delegated identity requires a valid Octop user and Agent ID")

    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": user_id,
        "agent_id": agent_id,
        "octop_instance_id": instance_id,
        "iat": now,
        "exp": now + 60,
        "jti": uuid.uuid4().hex,
    }
    if identity.trace_id:
        claims["octop_trace_id"] = str(identity.trace_id)[:128]
    # AKB rejects a replayed jti, so every request needs its own assertion —
    # the kid lets it pick the right public key while one is being rotated.
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": public_jwk(key)["kid"]})
