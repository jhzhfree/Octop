"""Read-only prerequisite checks. Never install, clone, start services or print secrets."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

MANIFEST = Path(__file__).with_name("projects.json")


def run(argv: list[str], cwd: Path) -> tuple[int, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GOTOOLCHAIN="local")
    try:
        result = subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=20, check=False
        )
        return result.returncode, result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def inspect(project: str, path: Path, runtime: bool = False) -> dict:
    spec = json.loads(MANIFEST.read_text())[project]
    checks = []
    head = None

    def record(name: str, ok: bool) -> None:
        checks.append({"check": name, "passed": ok})

    def git(*args: str) -> tuple[int, str]:
        return run(["git", "-C", str(path), *args], path)

    # Reject a subdirectory accidentally inheriting its parent's Git repository.
    code, top = git("rev-parse", "--show-toplevel")
    root_ok = code == 0 and Path(top.strip()).resolve() == path.resolve()
    record("repository_root", root_ok)
    if root_ok:
        code, candidate = git("rev-parse", "HEAD")
        if code == 0 and re.fullmatch(r"[0-9a-f]{40}", candidate.strip()):
            head = candidate.strip()
        record("valid_head", head is not None)
        code, origin = git("remote", "get-url", "origin")
        repo = spec["repository"]
        allowed = {f"https://github.com/{repo}{suffix}" for suffix in ("", ".git")}
        allowed |= {f"git@github.com:{repo}{suffix}" for suffix in ("", ".git")}
        record("origin_without_embedded_credentials", code == 0 and origin.strip() in allowed)
        code, branch = git("branch", "--show-current")
        record("expected_branch", code == 0 and branch.strip() == spec["branch"])
        code, _ = git("merge-base", "--is-ancestor", spec["baseline"], "HEAD")
        record("baseline_present_in_history", code == 0)
        code, status = git("status", "--porcelain", "--untracked-files=normal")
        record("clean_checkout", code == 0 and not status.strip())

    def probe(name: str, argv: list[str], pattern: str) -> None:
        code, output = run(argv, path)
        record(name, code == 0 and re.search(pattern, output) is not None)

    source_passed = all(check["passed"] for check in checks)
    if runtime and source_passed:
        # Explicit opt-in: these execute installed tools, but never install/start/pull.
        probe("docker_client", ["docker", "--version"], r"Docker version \d+")
        probe("docker_server", ["docker", "version", "--format", "{{.Server.Version}}"], r"^\d+\.")
        probe("compose_v2", ["docker", "compose", "version", "--short"], r"^v?2\.")
        if project == "Octop":
            probe("python_3_12_plus", ["python3", "--version"], r"Python 3\.(?:1[2-9]|[2-9]\d)\.")
            probe("uv", ["uv", "--version"], r"^uv \d+\.")
            probe("bubblewrap", ["bwrap", "--version"], r"bubblewrap \d+\.")
            code, _ = run(
                [
                    "bwrap",
                    "--unshare-all",
                    "--die-with-parent",
                    "--ro-bind",
                    "/",
                    "/",
                    "--",
                    "/bin/true",
                ],
                path,
            )
            record("bubblewrap_namespace_probe", code == 0)
        elif project == "mattermost":
            probe("go_1_26_10", ["go", "version"], r"^go version go1\.26\.10\s")
            probe("node_24_11_1", ["node", "--version"], r"^v24\.11\.1\s*$")
            probe("npm_11_6_2", ["npm", "--version"], r"^11\.6\.2\s*$")
        elif project == "akb":
            probe("java_8", ["java", "-version"], r'version "1\.8\.')
            probe("maven", ["mvn", "--version"], r"Apache Maven 3\.")
        elif project == "litellm":
            probe("python_3_10_to_15", ["python3", "--version"], r"Python 3\.1[0-5]\.")
    return {
        "project": project,
        "expected_branch": spec["branch"],
        "baseline": spec["baseline"],
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "runtime_probed": runtime and source_passed,
        "head": head,
        "deployment_verified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", choices=json.loads(MANIFEST.read_text()))
    parser.add_argument("path", type=Path)
    parser.add_argument("--runtime", action="store_true", help="Execute installed-tool probes only")
    args = parser.parse_args()
    report = inspect(args.project, args.path.resolve(), args.runtime)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
