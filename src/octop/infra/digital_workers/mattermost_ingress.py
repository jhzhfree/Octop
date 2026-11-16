"""Unwired broker intake service. Separate authentication, fresh source, local ACL.

No production composition or key registration exists. Only isolated tests provide
source readers and public-key mappings. This service has no executor or sender.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from octop.infra.db.repos.mattermost_ingress import IntakeError, MattermostIngressRepo
from octop.infra.digital_workers.mattermost_proof import ProofPolicy, ProofRejected, verify_proof

BROKER_DOMAIN = b"octop-mattermost-broker-request/v1\x00"


class SourceObservation(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)
    delivery_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    commitment: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["awaiting_octop_authorization", "cancelled"]
    receipt_expires_at: int = Field(gt=0, le=(1 << 63) - 1)
    version: int = Field(gt=0, le=(1 << 63) - 1)


SourceReader = Callable[[dict[str, Any], str], Awaitable[SourceObservation]]


class MattermostIngressService:
    def __init__(
        self, repo: MattermostIngressRepo, *, source: SourceReader | None, clock: Callable[[], int]
    ) -> None:
        self.repo = repo
        self.source = source
        self.clock = clock

    def authenticate(
        self, method: str, path: str, body: bytes, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        if self.source is None:
            raise IntakeError(503, "BROKER_ISOLATION_UNCONFIGURED")
        if len(body) > 32768:
            raise IntakeError(413, "BROKER_BODY_TOO_LARGE")
        broker = headers.get("x-octop-broker", "")
        timestamp = headers.get("x-octop-broker-time", "")
        nonce = headers.get("x-octop-broker-nonce", "")
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", broker)
            or not re.fullmatch(r"[1-9][0-9]{0,18}", timestamp)
            or not re.fullmatch(r"[0-9a-f]{64}", nonce)
        ):
            raise IntakeError(401, "BROKER_UNAUTHENTICATED")
        now = self.clock()
        issued = int(timestamp)
        if not issued <= now < issued + 30:
            raise IntakeError(401, "BROKER_AUTH_EXPIRED")
        mapping = self.repo.mapping(broker)
        try:
            shared_key = bytes.fromhex(mapping["broker_public_key"]) == bytes.fromhex(
                mapping["proof_public_key"]
            )
        except ValueError:
            raise IntakeError(503, "BROKER_ISOLATION_UNCONFIGURED") from None
        if shared_key:
            raise IntakeError(503, "BROKER_ISOLATION_UNCONFIGURED")
        # Fixed ASCII path/method; exact body digest. This is a proposed independent
        # broker profile, NOT the Mattermost proof signature and NOT a user JWT.
        message = (
            BROKER_DOMAIN
            + "\n".join(
                (broker, timestamp, nonce, method, path, hashlib.sha256(body).hexdigest())
            ).encode()
        )
        try:
            signature = base64.b64decode(headers.get("x-octop-broker-signature", ""), validate=True)
            if len(signature) != 64:
                raise ValueError
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(mapping["broker_public_key"])).verify(
                signature, message
            )
        except (ValueError, InvalidSignature):
            raise IntakeError(401, "BROKER_UNAUTHENTICATED") from None
        self.repo.consume_nonce(mapping, nonce, issued, self.clock)
        return mapping

    @staticmethod
    def _policy(mapping: dict[str, Any]) -> ProofPolicy:
        return ProofPolicy(
            enabled=True,
            key_id=mapping["proof_key_id"],
            public_key=bytes.fromhex(mapping["proof_public_key"]),
            binding=json.loads(mapping["binding_json"]),
            octop_actor_id=mapping["actor_id"],
            octop_organization_id=mapping["organization_id"],
            octop_employee_id=mapping["worker_id"],
        )

    async def _observe(self, mapping: dict[str, Any], receipt: str) -> SourceObservation:
        if self.source is None:
            raise IntakeError(503, "BROKER_ISOLATION_UNCONFIGURED")
        try:
            value = await asyncio.wait_for(self.source(mapping.copy(), receipt), timeout=2)
            if type(value) is not SourceObservation:
                raise ValueError
            return value
        except Exception:
            raise IntakeError(503, "SOURCE_UNAVAILABLE") from None

    @staticmethod
    def _match(source: SourceObservation, row: dict[str, Any], state: str) -> None:
        if (
            source.state != state
            or source.delivery_key != row["delivery_key"]
            or source.fingerprint != row["fingerprint"]
            or source.commitment != row["commitment"]
            or source.receipt_expires_at != row["source_expires_at"]
        ):
            raise IntakeError(403, "SOURCE_AUTHORITY_CHANGED")

    async def accept(self, mapping: dict[str, Any], raw: bytes) -> tuple[dict[str, Any], bool]:
        before = self.clock()
        try:
            policy = self._policy(mapping)
            candidate = verify_proof(raw, policy, now=before)
        except (ProofRejected, ValueError):
            raise IntakeError(422, "INTAKE_PROOF_INVALID") from None
        values = {
            "delivery_key": candidate.delivery_key,
            "fingerprint": candidate.fingerprint,
            "receipt": candidate.intent.receipt,
            "commitment": candidate.source_commitment(),
            "source_expires_at": candidate.intent.receipt_expires_at,
        }
        source = await self._observe(mapping, candidate.intent.receipt)
        self._match(source, values, "awaiting_octop_authorization")
        values["source_version"] = source.version
        # Recheck time and mapping after source I/O and again under transaction locks.
        return self.repo.accept(
            mapping,
            values,
            issued=max(before, candidate.proof_issued),
            expires=candidate.proof_expires,
            clock=self.clock,
        )

    async def cancel(
        self, mapping: dict[str, Any], key: str, revision: int, operation: str
    ) -> dict[str, Any]:
        observed = self.repo.inspect(mapping, key)
        source = await self._observe(mapping, observed["receipt"])
        self._match(source, observed, "cancelled")
        return self.repo.cancel(mapping, observed, revision, operation, source.version, self.clock)


def public_intake(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "delivery_key": row["delivery_key"],
        "fingerprint": row["fingerprint"],
        "state": row["state"],
        "revision": row["revision"],
        "source_version": str(row["source_version"]),
        "execution_authorized": False,
    }
