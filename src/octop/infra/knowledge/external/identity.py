"""Octop-signed identity assertion for AKB (``X-Octop-Identity``).

AKB can run in delegated-identity mode, where the shared API key identifies an
*application* principal and Octop additionally proves which instance and agent
are asking. The assertion is a short-lived, single-use JWT signed by this Octop
instance; AKB intersects the key's grants with the asserted identity.

Both sides must opt in: Octop sends the header only when
``config.akb_identity.enabled`` is true and ``instance_id`` is set, and AKB only
verifies it when its own ``EXTERNAL_OCTOP_IDENTITY_ENABLED`` is on.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

import jwt

logger = logging.getLogger(__name__)

IDENTITY_HEADER = "X-Octop-Identity"
# AKB stores secrets in its own table; Octop keeps the signing key next to its
# other control-plane secrets so a restore keeps assertions valid.
SECRET_KEY = "akb-identity"
# AKB rejects assertions older than 5 minutes (30s clock-skew tolerance).
TTL_SECONDS = 300


def assertion_token(
    *,
    secret: bytes,
    issuer: str,
    audience: str,
    instance_id: str,
    subject: str,
    agent_id: str = "",
) -> str:
    """Build one signed assertion. Never reuse a token: ``jti`` is single-use."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "octop_instance_id": instance_id,
        "agent_id": agent_id,
        "iat": now,
        "exp": now + TTL_SECONDS,
        "jti": uuid.uuid4().hex,
    }
    return str(jwt.encode(payload, secret, algorithm="HS256"))


def identity_token(
    config: Any,
    secret_repo: Any,
    *,
    subject: str,
    agent_id: str = "",
) -> str:
    """Return a fresh assertion, or ``""`` when delegation is not configured."""
    section = getattr(config, "akb_identity", None)
    if section is None or not getattr(section, "enabled", False):
        return ""
    instance_id = str(getattr(section, "instance_id", "") or "").strip()
    if not instance_id:
        logger.warning(
            "akb identity assertion enabled but instance_id is empty; falling back to API key only"
        )
        return ""
    try:
        secret = secret_repo.get_or_create(SECRET_KEY, lambda: os.urandom(32))
    except Exception:  # noqa: BLE001 - never break retrieval over identity plumbing
        logger.warning("akb identity secret unavailable; falling back to API key only")
        return ""
    return assertion_token(
        secret=bytes(secret),
        issuer=str(getattr(section, "issuer", "") or "octop"),
        audience=str(getattr(section, "audience", "") or "akb"),
        instance_id=instance_id,
        subject=str(subject),
        agent_id=str(agent_id),
    )
