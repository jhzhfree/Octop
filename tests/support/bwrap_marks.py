"""Shared pytest markers for bubblewrap / scoped-root_dir tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache

import pytest


# Real bubblewrap jail (subprocess + ro-bind); skip when the host/container
# has the binary but disallows the namespaces needed to run it.
@lru_cache(maxsize=1)
def _bwrap_namespace_available() -> bool:
    bwrap = shutil.which("bwrap")
    if sys.platform != "linux" or bwrap is None:
        return False
    try:
        probe = subprocess.run(
            [bwrap, "--ro-bind", "/", "/", "--", "/bin/true"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


linux_bwrap_only = pytest.mark.skipif(
    not _bwrap_namespace_available(),
    reason="bubblewrap jail requires Linux, bwrap, and namespace support",
)

# Tests that shell out with POSIX ``sh`` syntax (``mkdir -p``, ``cat``, …).
posix_shell_only = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX shell commands (sh/bash)",
)
