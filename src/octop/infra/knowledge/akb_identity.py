"""Short-lived Octop assertions for AKB delegated-user requests."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt


@dataclass(frozen=True)
class OctopIdentity:
    """Trusted identity taken from Octop's authenticated runtime context."""

    user_id: str
    agent_id: str
    trace_id: str = ""


def delegated_identity_enabled() -> bool:
    """Whether this Octop instance is provisioned to sign AKB assertions."""
    return os.environ.get("OCTOP_AKB_IDENTITY_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def issue_assertion(identity: OctopIdentity) -> str:
    """Sign the AKB `X-Octop-Identity` assertion with a one-minute lifetime."""
    issuer = os.environ.get("OCTOP_AKB_IDENTITY_ISSUER", "").strip()
    audience = os.environ.get("OCTOP_AKB_IDENTITY_AUDIENCE", "").strip()
    instance_id = os.environ.get("OCTOP_AKB_INSTANCE_ID", "").strip()
    private_key = os.environ.get("OCTOP_AKB_IDENTITY_PRIVATE_KEY", "").replace("\\n", "\n").strip()
    if not all((issuer, audience, instance_id, private_key)):
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
    key_id = os.environ.get("OCTOP_AKB_IDENTITY_KEY_ID", "").strip()
    headers = {"kid": key_id} if key_id else None
    return jwt.encode(claims, private_key, algorithm="RS256", headers=headers)
