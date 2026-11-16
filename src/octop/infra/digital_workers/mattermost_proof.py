"""Mattermost v2 proof decoding only; no broker authentication or intake authority.

Wire contract: jhzhfree/mattermost 4790d295, internal/handoff/proof.go.
Policy is receiver-owned input, never deserialize it from an inbound request.
No keys, source readers, routes, persistence, executor or sender are installed.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Annotated, Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_PROOF_BYTES = 32768
MAX_INT64 = (1 << 63) - 1
DOMAIN = b"mattermost-octop-intent-proof/v2\x00"
# Go strings.TrimSpace uses Unicode White_Space, not Python's extra C0 separators.
_GO_SPACE = "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
Token = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
MMID = Annotated[str, Field(pattern=r"^[a-z0-9]{26}$")]
HexID = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
PositiveInt64 = Annotated[int, Field(ge=1, le=MAX_INT64)]


class ProofRejected(Exception):
    def __init__(self) -> None:
        super().__init__("MATTERMOST_PROOF_REJECTED")


class _Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)


def _opaque(value: str) -> str:
    if not value.strip(_GO_SPACE) or not 0 < len(value.encode("utf-8")) <= 128:
        raise ValueError("invalid opaque identifier")
    return value


class ProofBinding(_Contract):
    instance: Token
    audience: Token
    organization: str
    employee: str
    user: MMID
    team: MMID
    channel: MMID
    bot: MMID

    _opaque_ids = field_validator("organization", "employee")(_opaque)

    @model_validator(mode="after")
    def distinct_user(self) -> ProofBinding:
        if self.user == self.bot:
            raise ValueError("source user must differ from bot")
        return self


class ProofIntent(_Contract):
    binding: ProofBinding
    receipt: HexID
    receipt_expires_at: PositiveInt64  # Source milliseconds; proof timestamps are seconds.
    goal: str = Field(max_length=2000, repr=False)
    mode: Literal["user_requested"]

    @field_validator("goal")
    @classmethod
    def valid_goal(cls, value: str) -> str:
        if not value.strip(_GO_SPACE) or len(value.encode("utf-8")) > 8000:
            raise ValueError("invalid goal")
        return value


class ProofPolicy(_Contract):
    """Locally resolved mapping; this is NOT an authenticated broker principal."""

    enabled: bool = False
    key_id: Token
    public_key: bytes = Field(min_length=32, max_length=32, repr=False)
    binding: ProofBinding
    octop_actor_id: PositiveInt64
    octop_organization_id: str
    octop_employee_id: str

    _opaque_ids = field_validator("octop_organization_id", "octop_employee_id")(_opaque)


class _Claims(_Contract):
    version: int = Field(ge=2, le=2)
    key_id: Token
    issued: PositiveInt64
    expires: PositiveInt64
    intent: ProofIntent = Field(repr=False)


class _Envelope(_Contract):
    payload: str = Field(repr=False)
    signature: str = Field(repr=False)


class ProofCandidate(_Contract):
    intent: ProofIntent = Field(repr=False)
    octop_actor_id: PositiveInt64
    octop_organization_id: str
    octop_employee_id: str
    delivery_key: HexID
    fingerprint: HexID

    def source_commitment(self) -> str:
        return hashlib.sha256(
            b"mattermost-source-observation/v1\x00" + _go_json(self.intent.model_dump())
        ).hexdigest()


def _go_json(value: Any) -> bytes:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for character in ("<", ">", "&", "\u2028", "\u2029"):
        encoded = encoded.replace(character, f"\\u{ord(character):04x}")
    return encoded.encode("utf-8")


def _decode_base64(value: str) -> bytes:
    decoded = base64.b64decode(value, validate=True)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("noncanonical base64")
    return decoded


def verify_proof(raw: bytes, policy: ProofPolicy, *, now: int) -> ProofCandidate:
    """Verify ordered Go JSON and signature; never return execution permission.

    Exact input bytes are required, including field order and HTML escaping.
    No JWT fallback, key discovery, source lookup, durable replay or side effects.
    Public errors omit goals, signatures, keys and raw input.
    """
    if (
        not policy.enabled
        or type(now) is not int
        or not 0 < now <= MAX_INT64
        or len(raw) > MAX_PROOF_BYTES
    ):
        raise ProofRejected()
    try:
        envelope = _Envelope.model_validate(json.loads(raw.decode("utf-8")))
        if _go_json(envelope.model_dump()) != raw:
            raise ValueError("noncanonical envelope")
        payload = _decode_base64(envelope.payload)
        signature = _decode_base64(envelope.signature)
        claims = _Claims.model_validate(json.loads(payload.decode("utf-8")))
        if _go_json(claims.model_dump()) != payload:
            raise ValueError("noncanonical claims")
        if (
            claims.key_id != policy.key_id
            or claims.intent.binding != policy.binding
            or not claims.issued <= now < claims.expires
            or not 0 < claims.expires - claims.issued <= 120
            or claims.expires > claims.intent.receipt_expires_at // 1000
            or len(signature) != 64
        ):
            raise ValueError("proof scope or time mismatch")
        Ed25519PublicKey.from_public_bytes(policy.public_key).verify(signature, DOMAIN + payload)
        intent = claims.intent
        identity = [
            "mattermost-octop-delivery/v1",
            intent.binding.instance,
            intent.binding.audience,
            intent.receipt,
        ]
        fingerprint = {
            "Intent": intent.model_dump(),
            "Actor": policy.octop_actor_id,
            "Organization": policy.octop_organization_id,
            "Employee": policy.octop_employee_id,
        }
        return ProofCandidate(
            intent=intent,
            octop_actor_id=policy.octop_actor_id,
            octop_organization_id=policy.octop_organization_id,
            octop_employee_id=policy.octop_employee_id,
            delivery_key=hashlib.sha256(_go_json(identity)).hexdigest(),
            fingerprint=hashlib.sha256(_go_json(fingerprint)).hexdigest(),
        )
    except (ValueError, TypeError, RecursionError, InvalidSignature):
        raise ProofRejected() from None
