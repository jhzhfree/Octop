"""Offline HTTP source-observation adapter; no production transport or key provider.

This proposed wire profile is separate from the existing Go v2 proof. The exact
MockTransport gate is intentional until deployment/credential isolation exists.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from octop.infra.digital_workers.mattermost_ingress import SourceObservation
from octop.infra.digital_workers.mattermost_proof import ProofBinding

REQUEST_DOMAIN = b"octop-mattermost-source-query/v1\x00"
RESPONSE_DOMAIN = b"mattermost-octop-source-observation/v1\x00"
SOURCE_URL = "https://mattermost-source.invalid/api/integrations/octop/v1/source-observations"
MAX_RESPONSE_BYTES = 16384


class SourceUnavailable(Exception):
    def __init__(self) -> None:
        super().__init__("MATTERMOST_SOURCE_UNAVAILABLE")


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)


class SourceTarget(_Contract):
    """Current deployment-owned scope, never deserialized from a client response."""

    broker_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    mapping_revision: int = Field(ge=1)
    binding: ProofBinding
    key_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    public_key: bytes = Field(min_length=32, max_length=32, repr=False)


class _WireObservation(_Contract):
    delivery_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    commitment: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["awaiting_octop_authorization", "cancelled"]
    receipt_expires_at: int = Field(ge=1, le=(1 << 63) - 1)
    version: str = Field(pattern=r"^[1-9][0-9]{0,18}$")

    @field_validator("version")
    @classmethod
    def int64_version(cls, value: str) -> str:
        if int(value) > (1 << 63) - 1:
            raise ValueError("source version out of range")
        return value


class _Payload(_Contract):
    protocol_version: int = Field(ge=1, le=1)
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: int = Field(ge=1, le=(1 << 63) - 1)
    expires_at: int = Field(ge=1, le=(1 << 63) - 1)
    observation: _WireObservation


class _Envelope(_Contract):
    key_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    payload: str = Field(repr=False)
    signature: str = Field(repr=False)


def _json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate source key")
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)


def _base64(value: str) -> bytes:
    decoded = base64.b64decode(value, validate=True)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("noncanonical base64")
    return decoded


TargetResolver = Callable[[dict[str, Any]], Awaitable[SourceTarget]]
RequestSigner = Callable[[SourceTarget, bytes], Awaitable[bytes]]


class OfflineMattermostSourceReader:
    def __init__(
        self,
        *,
        transport: httpx.MockTransport,
        resolve: TargetResolver,
        sign: RequestSigner,
        clock: Callable[[], int],
    ) -> None:
        if type(transport) is not httpx.MockTransport:
            raise SourceUnavailable()
        self._transport = transport
        self._resolve = resolve
        self._sign = sign
        self._clock = clock

    async def __call__(self, mapping: dict[str, Any], receipt: str) -> SourceObservation:
        # Recheck even if a local caller mutates the instance after construction.
        if type(self._transport) is not httpx.MockTransport:
            raise SourceUnavailable()
        try:
            async with asyncio.timeout(2):
                return await self._read(mapping, receipt)
        except Exception:
            # No upstream text, headers, signature or registry contents in errors/logs.
            raise SourceUnavailable() from None

    async def _read(self, mapping: dict[str, Any], receipt: str) -> SourceObservation:
        if len(receipt) != 64 or any(c not in "0123456789abcdef" for c in receipt):
            raise SourceUnavailable()
        target = await self._resolve(mapping.copy())
        if (
            type(target) is not SourceTarget
            or target.broker_id != mapping["broker_id"]
            or target.mapping_revision != mapping["revision"]
            or target.binding.model_dump() != json.loads(mapping["binding_json"])
        ):
            raise SourceUnavailable()
        broker_key = bytes.fromhex(mapping["broker_public_key"])
        if target.public_key in (broker_key, bytes.fromhex(mapping["proof_public_key"])):
            raise SourceUnavailable()
        started = self._clock()
        if type(started) is not int or not 0 < started <= (1 << 63) - 1:
            raise SourceUnavailable()
        request_body = json.dumps(
            {
                "protocol_version": 1,
                "broker_id": target.broker_id,
                "nonce": secrets.token_hex(32),
                "issued_at": started,
                "binding": target.binding.model_dump(),
                "receipt": receipt,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        signature = await self._sign(target, REQUEST_DOMAIN + request_body)
        Ed25519PublicKey.from_public_bytes(broker_key).verify(
            signature, REQUEST_DOMAIN + request_body
        )
        if (
            await self._resolve(mapping.copy()) != target
            or not started <= self._clock() < started + 5
        ):
            raise SourceUnavailable()
        async with (
            httpx.AsyncClient(
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
                verify=True,
                timeout=1,
            ) as client,
            client.stream(
                "POST",
                SOURCE_URL,
                content=request_body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "X-Octop-Source-Broker": target.broker_id,
                    "X-Octop-Source-Signature": base64.b64encode(signature).decode("ascii"),
                },
            ) as response,
        ):
            if (
                response.status_code != 200
                or response.headers.get("content-type", "").split(";")[0].strip().lower()
                != "application/json"
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise SourceUnavailable()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise SourceUnavailable()
                raw.extend(chunk)
        envelope = _Envelope.model_validate(_json(bytes(raw)))
        if envelope.key_id != target.key_id:
            raise SourceUnavailable()
        payload = _base64(envelope.payload)
        Ed25519PublicKey.from_public_bytes(target.public_key).verify(
            _base64(envelope.signature), RESPONSE_DOMAIN + payload
        )
        value = _Payload.model_validate(_json(payload))
        now = self._clock()
        if (
            value.request_sha256 != hashlib.sha256(request_body).hexdigest()
            or not started <= value.observed_at <= now < value.expires_at
            or not 0 < value.expires_at - value.observed_at <= 5
            or now - started >= 5
        ):
            raise SourceUnavailable()
        if (
            await self._resolve(mapping.copy()) != target
            or not now <= self._clock() < value.expires_at
        ):
            raise SourceUnavailable()
        return SourceObservation.model_validate(
            value.observation.model_dump() | {"version": int(value.observation.version)}
        )
