"""Deployment configuration and unwired, read-only identity orchestration.

No route or server startup uses this module. A deployment-owned external secret
provider must bind a credential to the complete configuration, including revision,
before production activation. The generic same-DB connector crypto is NOT used.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from octop.infra.db.services import RepoBundle
from octop.infra.digital_workers.mattermost_verification import (
    IdentityObservation,
    TrustedMattermostTarget,
    VerificationScope,
    observe_bot_identity,
)


class InstanceConfiguration(BaseModel):
    """Non-secret manifest accepted only from a trusted local deployment operator."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)
    organization_id: str = Field(min_length=1, max_length=128)
    instance_key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    origin: str
    bot_id: str = Field(pattern=r"^[a-z0-9]{26}$")
    credential_ref: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$", repr=False)
    status: Literal["configured", "revoked"]

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return TrustedMattermostTarget.validate_origin(value)


class CredentialBinding(BaseModel):
    """Trusted provider response, never HTTP input, serialization, or DB storage.

    Provider must independently authorize this exact tuple, not just look up a ref
    from the DB. Otherwise a rewritten origin could steal an existing credential.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, hide_input_in_errors=True)
    configuration: InstanceConfiguration
    revision: int = Field(ge=1)
    token: SecretStr = Field(repr=False, exclude=True)


def configure_instance(
    repos: RepoBundle,
    *,
    actor_id: int,
    expected_revision: int,
    configuration: InstanceConfiguration,
) -> int | None:
    if type(expected_revision) is not int or not 0 <= expected_revision < 9223372036854775807:
        raise ValueError("invalid revision")
    return repos.mattermost_instance_repo.configure(
        actor_id=actor_id, expected_revision=expected_revision, **configuration.model_dump()
    )


async def observe_configured_identity(
    repos: RepoBundle,
    scope: VerificationScope,
    *,
    resolve_credential: Callable[[InstanceConfiguration, int], Awaitable[CredentialBinding]],
    transport: httpx.MockTransport,
) -> IdentityObservation:
    """Offline integration seam ONLY. No production transport option yet.

    Explicit MockTransport keeps this incomplete integration fail-closed. A future
    live adapter needs a reviewed external secret provider and revocation policy.
    No success persists a binding or changes the credential-free intent.
    """
    if type(transport) is not httpx.MockTransport:
        raise ValueError("offline transport required")

    async def current_configuration() -> InstanceConfiguration | None:
        row = await asyncio.to_thread(
            repos.mattermost_instance_repo.current_configuration, **scope.model_dump()
        )
        if row is None:
            return None
        return InstanceConfiguration.model_validate(
            {key: row[key] for key in InstanceConfiguration.model_fields}
        )

    result = "unavailable"
    try:
        configuration = await current_configuration()
        if configuration is None:
            result = "access_changed"
        else:
            # Before credential resolution, then again immediately before the mock GET.
            async with asyncio.timeout(10):
                credential = await resolve_credential(configuration, scope.configuration_revision)
            if (
                credential.configuration != configuration
                or credential.revision != scope.configuration_revision
            ):
                result = "access_changed"
            else:

                async def scope_is_current(candidate: VerificationScope) -> bool:
                    return candidate == scope and await current_configuration() == configuration

                target = TrustedMattermostTarget(
                    scope=scope,
                    origin=configuration.origin,
                    bot_id=configuration.bot_id,
                    token=credential.token,
                )
                observation = await observe_bot_identity(
                    target, scope_is_current=scope_is_current, transport=transport
                )
                result = observation.result
    except Exception:
        # Provider errors may embed credentials. Never log or expose them.
        result = "unavailable"
    # Audit failure propagates without returning success; no false durable grant.
    result = await asyncio.to_thread(
        repos.mattermost_instance_repo.audit_observation, scope=scope.model_dump(), result=result
    )
    return IdentityObservation.model_validate({"scope": scope, "result": result})
