#!/bin/sh
# No apt/sudo/package download. Failed prerequisite probes stop startup.
# This probe does NOT wrap Octop or every agent execution in bubblewrap.
set -eu
if [ "${OCTOP_LOCAL_TRIAL:-}" != "1" ]; then
    echo 'Local trial policy is required.' >&2
    exit 1
fi
if ! command -v bwrap >/dev/null 2>&1; then
    echo 'Missing preinstalled bubblewrap. Stop: do not auto-install or disable sandbox.' >&2
    exit 1
fi
# Actual namespace check, not just a binary check. Restrictive hosts may refuse it.
if ! bwrap --unshare-all --die-with-parent --ro-bind / / -- /bin/true; then
    echo 'Sandbox probe failed. Stop: do not add privileged mode or loosen host security.' >&2
    exit 1
fi
exec /usr/local/bin/docker-entrypoint.sh octop run --host 0.0.0.0 --port 8088 --workers 1
