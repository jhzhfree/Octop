"""Regression checks for the human-reviewable enterprise scene contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_SCENE_TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "src/octop/infra/agents/experts/library/enterprise-work-assistant/skills/"
    / "enterprise-scene-builder/references/scene-template.yaml"
)


def _load_scene() -> dict[str, Any]:
    data = yaml.safe_load(_SCENE_TEMPLATE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_scene_template_has_unique_ordered_steps_and_explicit_scope() -> None:
    scene = _load_scene()

    assert scene["schema_version"] == 1
    assert scene["id"] and scene["owner"]
    assert scene["scope"]["assets"]
    assert scene["scope"]["allowlist_source"]
    assert scene["identity"]["secret_storage"] == "connector_managed"
    assert scene["identity"]["never_prompt_for_password_or_mfa"] is True

    steps = scene["steps"]
    step_ids = [step["id"] for step in steps]
    assert len(step_ids) == len(set(step_ids))
    assert step_ids.index("authorize") == 0
    assert step_ids.index("collect") < step_ids.index("evaluate") < step_ids.index("report")


def test_scene_template_defaults_to_read_only_and_fails_closed() -> None:
    scene = _load_scene()

    assert scene["execution"]["mode"] == "manual_reviewed"
    assert scene["execution"]["duplicate_policy"] == "skip_existing_run"
    assert scene["execution"]["concurrency_policy"] == "forbid_overlapping_runs"
    assert scene["policy"]["default_access"] == "read_only"
    assert scene["policy"]["auto_remediation"] is False
    assert scene["policy"]["fail_closed_on_authorization_error"] is True
    assert scene["policy"]["retry_only_transient_failures"] is True
    assert {
        "production_change",
        "service_restart",
        "asset_scope_change",
        "vulnerability_scan",
    }.issubset(set(scene["policy"]["required_approval_actions"]))


def test_scene_template_keeps_external_notification_disabled_by_default() -> None:
    scene = _load_scene()
    notify_steps = [step for step in scene["steps"] if step["action"].startswith("send_")]

    assert notify_steps
    assert all(step.get("enabled") is False for step in notify_steps)
    assert all(step.get("requires_destination_allowlist") is True for step in notify_steps)
    assert scene["policy"]["audit_run"] is True
