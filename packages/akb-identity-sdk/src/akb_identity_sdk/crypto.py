"""Portable RSA primitives. No filesystem, environment or platform state."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def generate_uuid() -> str:
    return str(uuid.uuid4())


def validate_uuid(value: str) -> str:
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Canonical lowercase UUID required")
    return value


def _check(key: rsa.RSAPrivateKey | rsa.RSAPublicKey) -> None:
    if not 3072 <= key.key_size <= 4096:
        raise ValueError("RSA 3072–4096 key required")
    public = key.public_key() if isinstance(key, rsa.RSAPrivateKey) else key
    if public.public_numbers().e != 65537:
        raise ValueError("unsupported RSA exponent")


def private_key(material: str | rsa.RSAPrivateKey) -> rsa.RSAPrivateKey:
    key = (
        serialization.load_pem_private_key(material.encode(), password=None)
        if isinstance(material, str)
        else material
    )
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("RSA private key required")
    _check(key)
    return key


def public_key(
    material: str | dict[str, Any] | rsa.RSAPublicKey | rsa.RSAPrivateKey,
) -> rsa.RSAPublicKey:
    key: Any
    if isinstance(material, rsa.RSAPrivateKey):
        key = material.public_key()
    elif isinstance(material, rsa.RSAPublicKey):
        key = material
    elif isinstance(material, dict):
        if material.get("kty") != "RSA" or any(
            k in material for k in ("d", "p", "q", "dp", "dq", "qi", "oth")
        ):
            raise ValueError("Public RSA JWK required")

        def number(value: str) -> int:
            return int.from_bytes(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)), "big")

        key = rsa.RSAPublicNumbers(number(material["e"]), number(material["n"])).public_key()
    else:
        key = serialization.load_pem_public_key(material.encode())
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError("RSA public key required")
    _check(key)
    return key


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def public_jwk(material: str | rsa.RSAPrivateKey | rsa.RSAPublicKey) -> dict[str, str]:
    # Preserve Octop's existing PEM-private-key import interface.
    key = (
        private_key(material).public_key()
        if isinstance(material, str) and "PRIVATE KEY" in material
        else public_key(material)
    )
    numbers = key.public_numbers()

    def encode(n: int) -> str:
        return _b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))

    core = {"e": encode(numbers.e), "kty": "RSA", "n": encode(numbers.n)}
    kid = _b64(
        hashlib.sha256(json.dumps(core, separators=(",", ":"), sort_keys=True).encode()).digest()
    )
    return {**core, "kid": kid, "alg": "RS256", "use": "sig"}


@dataclass(frozen=True)
class KeyPair:
    private_pem: str = field(repr=False)
    public_pem: str
    public_jwk: dict[str, str]
    kid: str


def generate_key_pair(bits: int = 3072) -> KeyPair:
    if bits not in (3072, 4096):
        raise ValueError("Choose RSA 3072 or 4096 bits")
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    jwk = public_jwk(key)
    return KeyPair(
        private_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        public_pem=key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
        public_jwk=jwk,
        kid=jwk["kid"],
    )


def sign_data(data: bytes, key: str | rsa.RSAPrivateKey) -> str:
    if not isinstance(data, bytes):
        raise TypeError("Sign exact bytes")
    return _b64(private_key(key).sign(data, padding.PKCS1v15(), hashes.SHA256()))


def verify_signature(
    data: bytes, signature: str, key: str | dict[str, Any] | rsa.RSAPublicKey
) -> bool:
    if not isinstance(data, bytes):
        raise TypeError("Verify exact bytes")
    public = public_key(key)
    try:
        decoded = base64.b64decode(
            signature + "=" * (-len(signature) % 4), altchars=b"-_", validate=True
        )
        public.verify(decoded, data, padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, InvalidSignature):
        return False
    return True
