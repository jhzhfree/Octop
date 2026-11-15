"""Unwired read-only verification kernel, not an authorization or public API.

Only deployment-owned configuration may construct a target. A production adapter
must implement the scope/revocation callback before this can be exposed to users.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from collections.abc import Awaitable, Callable
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class VerificationScope(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    organization_id: str
    worker_id: str
    actor_id: int
    worker_version: int = Field(ge=1)
    intent_revision: int = Field(ge=1)
    configuration_revision: int = Field(ge=1)
    instance_key: str


class TrustedMattermostTarget(BaseModel):
    """Server-only configuration; never deserialize this from an HTTP request.

    This first kernel accepts global literal IP origins only. HTTPS certificate
    validation must succeed for that IP (IP SAN). DNS and private deployments are
    deliberately not supported yet, rather than weakening their outbound policy.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)
    scope: VerificationScope
    origin: str
    bot_id: str = Field(pattern=r"^[a-z0-9]{26}$")
    token: SecretStr = Field(repr=False, exclude=True)

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            address = ipaddress.ip_address(parsed.hostname or "")
            host = f"[{address}]" if address.version == 6 else str(address)
            port = parsed.port
            canonical = f"https://{host}" + (f":{port}" if port is not None else "")
            if (
                value != canonical
                or not address.is_global
                or address.is_multicast
                or address.is_reserved
                or "%" in value
                or port == 0
            ):
                raise ValueError
        except ValueError:
            raise ValueError("invalid trusted origin") from None
        return value

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", value.get_secret_value()):
            raise ValueError("invalid credential")
        return value


class IdentityObservation(BaseModel):
    model_config = ConfigDict(frozen=True)
    scope: VerificationScope
    result: Literal["bot_identity_observed", "identity_rejected", "unavailable", "access_changed"]
    operation_mode: Literal["human_initiated_read_only_verification"] = (
        "human_initiated_read_only_verification"
    )
    consistency: Literal["observation_not_atomic"] = "observation_not_atomic"
    execution_authorized: Literal[False] = False
    human_initiated_send_allowed: Literal[False] = False
    automatic_send_allowed: Literal[False] = False


async def observe_bot_identity(
    target: TrustedMattermostTarget,
    *,
    scope_is_current: Callable[[VerificationScope], Awaitable[bool]],
    transport: httpx.MockTransport | None = None,
) -> IdentityObservation:
    """Fixed GET /me only; no writes, credential discovery, or authorization grant.

    The required callback must re-read enabled actor/admin or manager permissions,
    organization/worker/Agent ownership, draft intent and all revisions including
    credential/config revocation. It runs before sending and after receiving.
    A positive result is a time-bound observation, never an atomic permission.
    ``transport`` is solely an offline test seam; arbitrary clients are not accepted.
    """
    result: Literal["bot_identity_observed", "identity_rejected", "unavailable", "access_changed"]
    result = "unavailable"
    try:
        async with asyncio.timeout(10):
            if await scope_is_current(target.scope) is not True:
                return IdentityObservation(scope=target.scope, result="access_changed")
            async with (
                httpx.AsyncClient(
                    transport=transport,
                    trust_env=False,
                    follow_redirects=False,
                    verify=True,
                    timeout=5,
                ) as client,
                client.stream(
                    "GET",
                    target.origin + "/api/v4/users/me",
                    headers={
                        "Authorization": "Bearer " + target.token.get_secret_value(),
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                ) as response,
            ):
                # Reject before consuming any untrusted error/redirect body.
                if (
                    response.status_code == 200
                    and response.headers.get("content-type", "").split(";")[0].strip().lower()
                    == "application/json"
                    and response.headers.get("content-encoding", "identity") == "identity"
                ):
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > 16384:
                            raise ValueError("response limit")
                        content.extend(chunk)
                    data = json.loads(content)
                    if (
                        isinstance(data, dict)
                        and data.get("id") == target.bot_id
                        and data.get("is_bot") is True
                        and type(data.get("delete_at")) is int
                        and data["delete_at"] == 0
                        and data.get("roles") == "system_user"
                    ):
                        result = "bot_identity_observed"
                    else:
                        result = "identity_rejected"
                else:
                    result = "identity_rejected"
            if await scope_is_current(target.scope) is not True:
                result = "access_changed"
    except Exception:
        # Never log/return exception repr, request headers, or upstream bodies.
        result = "unavailable"
    return IdentityObservation(scope=target.scope, result=result)
