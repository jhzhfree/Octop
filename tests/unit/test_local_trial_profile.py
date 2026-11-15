"""Offline contract checks; these do not prove a Docker deployment succeeds."""

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_compose_is_separate_fail_closed_profile() -> None:
    profile = yaml.safe_load((ROOT / "docker/local-trial/compose.yml").read_text())
    service = profile["services"]["octop"]
    assert service["ports"] == ["127.0.0.1:8088:8088"]
    assert service["pull_policy"] == "never"
    assert "build" not in service
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert profile["networks"] == {"trial": {"internal": True}}
    assert service["networks"] == ["trial"]
    assert service["volumes"] == ["trial-data:/data", "./preflight.sh:/trial/preflight.sh:ro"]
    env = service["environment"]
    assert env["OCTOP_LOCAL_TRIAL"] == "1"
    assert env["LANGFUSE_TRACING_ENABLED"] == "false"
    assert env["OTEL_SDK_DISABLED"] == "true"
    assert env["OCTOP_DEFAULT_PASSWORD"] == ""
    assert not any("KEY" in key or "TOKEN" in key for key in env)
    assert "env_file" not in service


def test_missing_bwrap_never_installs_in_trial(monkeypatch: pytest.MonkeyPatch) -> None:
    from octop.infra.utils import bwrap

    monkeypatch.setenv("OCTOP_LOCAL_TRIAL", "1")
    monkeypatch.setattr(bwrap.sys, "platform", "linux")
    monkeypatch.setattr(bwrap.shutil, "which", lambda _: None)
    installer = Mock(side_effect=AssertionError("package install forbidden"))
    monkeypatch.setattr(bwrap, "_detect_package_manager", installer)
    assert bwrap.ensure_bubblewrap()["reason"] == "local_trial_no_install"
    installer.assert_not_called()


@pytest.mark.asyncio
async def test_trial_blocks_scheduled_and_direct_proactive_care(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octop.infra.proactive.scheduler import ProactiveCareScheduler
    from octop.infra.proactive.service import ProactiveCareService

    monkeypatch.setenv("OCTOP_LOCAL_TRIAL", "1")
    # No collaborators: any attempt to access persistence/model/gateway must fail.
    scheduler = object.__new__(ProactiveCareScheduler)
    scheduler._suspended = False
    scheduler.ensure_scheduled("demo")
    scheduler._schedule("demo")
    service = object.__new__(ProactiveCareService)
    await service.run("demo", "session")


def test_preflight_requires_real_namespace_probe_before_entrypoint() -> None:
    script = (ROOT / "docker/local-trial/preflight.sh").read_text()
    assert "command -v bwrap" in script
    probe = "bwrap --unshare-all --die-with-parent --ro-bind / / -- /bin/true"
    assert probe in script
    assert script.index(probe) < script.index("exec /usr/local/bin/docker-entrypoint.sh")
    assert script.count("exit 1") == 3


@pytest.mark.skipif(os.name != "posix", reason="POSIX container entrypoint")
@pytest.mark.parametrize("mode", ["unset", "missing", "broken"])
def test_preflight_refuses_unsafe_start(tmp_path: Path, mode: str) -> None:
    env = {"PATH": str(tmp_path)}
    if mode != "unset":
        env["OCTOP_LOCAL_TRIAL"] = "1"
    if mode == "broken":
        binary = tmp_path / "bwrap"
        binary.write_text("#!/bin/sh\nexit 1\n")
        binary.chmod(0o700)
    result = subprocess.run(
        ["/bin/sh", str(ROOT / "docker/local-trial/preflight.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "docker-entrypoint" not in result.stderr
