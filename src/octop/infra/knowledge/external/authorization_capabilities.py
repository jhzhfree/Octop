"""Untrusted deployment capability reports are diagnostics, never authorization."""

from typing import Any


def parse_akb_authorization_capabilities(capabilities: object) -> dict[str, Any]:
    """Recognize only the implemented v1 contract; never unlock retrieval."""
    result: dict[str, Any] = {
        "status": "unknown",
        "reason_code": "CAPABILITY_UNKNOWN",
        "retrieval_authorized": False,
        "user_acl_verified": False,
    }
    if not isinstance(capabilities, dict):
        return result
    experimental = capabilities.get("experimental")
    if not isinstance(experimental, dict):
        return result
    report = experimental.get("akb.authorization")
    if not isinstance(report, dict):
        return result
    expected = {
        "schema_version": 1,
        "workload_v2_user_delegation_supported": False,
        "workload_content_tag_enforcement": True,
        "explicit_external_user_binding_required": True,
        "empty_tag_grant_semantics": "PUBLIC_ONLY",
        "capability_is_authorization": False,
    }
    for key, value in expected.items():
        actual = report.get(key)
        if type(actual) is not type(value) or actual != value:
            return result
    legacy = report.get("legacy_agent_delegation_configured")
    mode = report.get("external_content_tag_mode")
    if type(legacy) is not bool or not isinstance(mode, str):
        return result
    if mode not in {"ENFORCE", "SHADOW", "CLIENT"}:
        return result
    # Even an ENFORCE deployment has not proven this caller's content ACL.
    result.update(
        status="configured_not_verified" if legacy and mode == "ENFORCE" else "unsupported",
        reason_code="USER_ACL_NOT_VERIFIED"
        if legacy and mode == "ENFORCE"
        else "DUAL_SUBJECT_ENFORCEMENT_UNAVAILABLE",
        external_content_tag_mode=mode,
        legacy_agent_delegation_configured=legacy,
        workload_v2_user_delegation_supported=False,
        empty_tag_grant_semantics="PUBLIC_ONLY",
    )
    return result
