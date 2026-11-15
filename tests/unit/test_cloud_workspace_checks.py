"""Offline tests for the read-only preparation tool; no deployment is performed."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("cloud_check", ROOT / "dev/cloud-workspace/check.py")
assert SPEC and SPEC.loader
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
PROJECTS = json.loads(CHECK.MANIFEST.read_text())


def fake_runner(path, project, overrides=None):
    spec = PROJECTS[project]
    answers = {
        ("rev-parse", "--show-toplevel"): (0, str(path)),
        ("rev-parse", "HEAD"): (0, spec["baseline"]),
        ("remote", "get-url", "origin"): (0, f"https://github.com/{spec['repository']}.git"),
        ("branch", "--show-current"): (0, spec["branch"]),
        ("merge-base", "--is-ancestor", spec["baseline"], "HEAD"): (0, ""),
        ("status", "--porcelain", "--untracked-files=normal"): (0, ""),
    }
    answers.update(overrides or {})
    calls = []

    def run(argv, cwd):
        calls.append(argv)
        if argv[0] == "git":
            return answers[tuple(argv[3:])]
        return 127, "secret stderr must not be reported"

    return run, calls


@pytest.mark.parametrize("project", PROJECTS)
def test_source_check_is_repeatable_read_only(monkeypatch, tmp_path, project):
    runner, calls = fake_runner(tmp_path, project)
    monkeypatch.setattr(CHECK, "run", runner)
    first = CHECK.inspect(project, tmp_path)
    assert first == CHECK.inspect(project, tmp_path)
    assert first["passed"] and not first["runtime_probed"]
    assert first["deployment_verified"] is False
    assert all(argv[0] == "git" for argv in calls)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "command,value",
    [
        (("branch", "--show-current"), "main"),
        (("branch", "--show-current"), ""),
        (("remote", "get-url", "origin"), "https://token-secret@github.com/jhzhfree/Octop.git"),
        (("status", "--porcelain", "--untracked-files=normal"), " M source.py"),
        (("rev-parse", "--show-toplevel"), "/wrong/parent"),
    ],
)
def test_wrong_checkout_fails_without_leaking_output(monkeypatch, tmp_path, command, value):
    runner, _ = fake_runner(tmp_path, "Octop", {command: (0, value)})
    monkeypatch.setattr(CHECK, "run", runner)
    report = CHECK.inspect("Octop", tmp_path)
    assert not report["passed"]
    assert "token-secret" not in json.dumps(report)


def test_missing_baseline_and_tools_fail_closed(monkeypatch, tmp_path):
    baseline = PROJECTS["Octop"]["baseline"]
    runner, calls = fake_runner(
        tmp_path, "Octop", {("merge-base", "--is-ancestor", baseline, "HEAD"): (1, "")}
    )
    monkeypatch.setattr(CHECK, "run", runner)
    report = CHECK.inspect("Octop", tmp_path, runtime=True)
    assert not report["passed"]
    assert not report["runtime_probed"]
    assert all(argv[0] == "git" for argv in calls)
    assert not any(word in argv for argv in calls for word in ("install", "pull", "up", "clone"))
    assert "secret stderr" not in json.dumps(report)


def test_go_probe_disables_automatic_toolchain_download(monkeypatch, tmp_path):
    captured = {}

    def subprocess_run(argv, **kwargs):
        captured.update(kwargs)
        raise CHECK.subprocess.TimeoutExpired(argv, 20)

    monkeypatch.setattr(CHECK.subprocess, "run", subprocess_run)
    assert CHECK.run(["go", "version"], tmp_path) == (127, "")
    assert captured["env"]["GOTOOLCHAIN"] == "local"
    assert captured["timeout"] == 20


def test_missing_runtime_tools_fail_closed(monkeypatch, tmp_path):
    runner, calls = fake_runner(tmp_path, "Octop")
    monkeypatch.setattr(CHECK, "run", runner)
    report = CHECK.inspect("Octop", tmp_path, runtime=True)
    assert not report["passed"] and report["runtime_probed"]
    assert any("--unshare-all" in argv for argv in calls)
    assert "secret stderr" not in json.dumps(report)


@pytest.mark.parametrize(
    "broken",
    [None, "go", "node", "npm", "docker"],
)
def test_mattermost_versions_are_exact_and_fail_closed(monkeypatch, tmp_path, broken):
    git_runner, _ = fake_runner(tmp_path, "mattermost")
    outputs = {
        ("docker", "--version"): "Docker version 27.1.0",
        ("docker", "version", "--format", "{{.Server.Version}}"): "27.1.0",
        ("docker", "compose", "version", "--short"): "2.30.0",
        ("go", "version"): "go version go1.26.10 linux/amd64",
        ("node", "--version"): "v24.11.1\n",
        ("npm", "--version"): "11.6.2\n",
    }

    def runner(argv, cwd):
        if argv[0] == "git":
            return git_runner(argv, cwd)
        if argv[0] == broken:
            return 0, "unexpected version or executable"
        return 0, outputs[tuple(argv)]

    monkeypatch.setattr(CHECK, "run", runner)
    report = CHECK.inspect("mattermost", tmp_path, runtime=True)
    assert report["passed"] is (broken is None)
    assert report["deployment_verified"] is False
