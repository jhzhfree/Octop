"""Deployment reports cannot enable knowledge retrieval or prove user ACL."""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octop.infra.knowledge.external.adapters import akb_mcp
from octop.infra.knowledge.external.authorization_capabilities import (
    parse_akb_authorization_capabilities as parse,
)
from octop.infra.knowledge.external.types import ExternalSourceSpec


def capability() -> dict[str, Any]:
    return {
        "experimental": {
            "akb.authorization": {
                "schema_version": 1,
                "workload_v2_user_delegation_supported": False,
                "legacy_agent_delegation_configured": True,
                "external_content_tag_mode": "ENFORCE",
                "workload_content_tag_enforcement": True,
                "explicit_external_user_binding_required": True,
                "empty_tag_grant_semantics": "PUBLIC_ONLY",
                "capability_is_authorization": False,
            }
        }
    }


@pytest.mark.parametrize(
    "raw",
    [None, [], "true", {}, {"experimental": []}, {"experimental": {"akb.authorization": True}}],
)
def test_old_or_malformed_is_unknown(raw: object) -> None:
    assert parse(raw) == {
        "status": "unknown",
        "reason_code": "CAPABILITY_UNKNOWN",
        "retrieval_authorized": False,
        "user_acl_verified": False,
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("schema_version", "1"),
        ("workload_v2_user_delegation_supported", True),
        ("workload_content_tag_enforcement", "true"),
        ("explicit_external_user_binding_required", False),
        ("empty_tag_grant_semantics", "DENY_ALL"),
        ("capability_is_authorization", True),
        ("legacy_agent_delegation_configured", 1),
        ("external_content_tag_mode", "UNKNOWN"),
        ("external_content_tag_mode", []),
    ],
)
def test_unknown_version_types_and_contradictions(key: str, value: object) -> None:
    raw = capability()
    raw["experimental"]["akb.authorization"][key] = value
    assert parse(raw)["status"] == "unknown"
    assert parse(raw)["retrieval_authorized"] is False


def test_every_required_field_is_required() -> None:
    original = capability()
    for key in original["experimental"]["akb.authorization"]:
        raw = deepcopy(original)
        del raw["experimental"]["akb.authorization"][key]
        assert parse(raw)["status"] == "unknown"


@pytest.mark.parametrize(
    "mode,legacy,status",
    [
        ("ENFORCE", True, "configured_not_verified"),
        ("ENFORCE", False, "unsupported"),
        ("SHADOW", True, "unsupported"),
        ("CLIENT", True, "unsupported"),
    ],
)
def test_valid_deployments_never_authorize(mode: str, legacy: bool, status: str) -> None:
    raw = capability()
    raw["experimental"]["akb.authorization"].update(
        external_content_tag_mode=mode, legacy_agent_delegation_configured=legacy
    )
    raw["experimental"]["akb.authorization"]["retrieval_authorized"] = True
    result = parse(raw)
    assert result["status"] == status
    assert result["retrieval_authorized"] is False
    assert result["user_acl_verified"] is False
    assert result["workload_v2_user_delegation_supported"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [{}, capability(), {"experimental": "bad"}])
async def test_probe_compatibility_no_retrieval(
    monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]
) -> None:
    initialize = AsyncMock(return_value={"capabilities": raw, "protocolVersion": "2025-06-18"})
    rpc = AsyncMock(return_value={"tools": [{"name": "knowledge_search", "inputSchema": {}}]})
    monkeypatch.setattr(akb_mcp, "_initialize", initialize)
    monkeypatch.setattr(akb_mcp, "_rpc", rpc)
    spec = ExternalSourceSpec(
        binding_id="source", display_name="AKB", kind="akb-mcp", endpoint="https://akb.example"
    )
    result = await akb_mcp.probe(spec, {"auth_type": "agent"})
    assert result.ok is True
    assert result.capabilities == raw
    assert result.authorization_support["retrieval_authorized"] is False
    assert result.authorization_support["user_acl_verified"] is False
    assert rpc.await_count == 1
    assert rpc.call_args.args[3] == "tools/list"


def test_additional_sensitive_fields_are_not_copied_into_diagnostic() -> None:
    raw = capability()
    raw["experimental"]["akb.authorization"].update(
        secret="never-copy",
        user_id="other-user",
        knowledge_base_id="KB_other",
        retrieval_authorized=True,
        user_acl_verified=True,
    )
    result = parse(raw)
    assert not {"secret", "user_id", "knowledge_base_id"}.intersection(result)
    assert result["retrieval_authorized"] is False
    assert result["user_acl_verified"] is False
