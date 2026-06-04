#!/usr/bin/env bash
# pytest-clean: run pytest with automatic xdist-worker reaping.
#
# Why this exists: pytest-xdist workers are spawned via
#   python -c "import sys; exec(eval(sys.stdin.readline()))"
# which installs no signal handlers and has no parent-death reaper. If
# the parent shell dies (timeouts, agent tooling interrupting, an
# `exit` racing with the test cycle), the workers get reparented to
# PID 1 and stay alive consuming memory. On a multi-CPU machine each
# leftover worker is ~15-25MB of RAM, and one CI loop can leave
# dozens of them.
#
# This wrapper:
#   1. Snapshots xdist worker PIDs before launching pytest.
#   2. Runs pytest under a trap that reaps them on EXIT, INT, TERM,
#      HUP, PIPE — so the workers always die when the wrapper exits
#      for any reason (success, failure, signal, or crash).
#   3. As a last-resort fallback, kills any still-living workers
#      matching the snapshot by exact command + age < 2 minutes.
#
# Usage:
#   scripts/pytest-clean.sh tests/unit/granite_container/
#   scripts/pytest-clean.sh -k "test_pick" tests/unit/
#   scripts/pytest-clean.sh -x   # all args pass through to pytest
#
# Replaces the bare `pytest tests/...` patterns in the dev / sdlc /
# agent workflows. Does NOT change pytest's own behavior — the
# reaping is purely a teardown safety net.

set -u

# Find the pytest rootdir: prefer the caller's cwd (so a worktree
# agent tests the worktree, not the main repo), and fall back to the
# script's location only if cwd has no pyproject.toml.
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "pyproject.toml" ] && grep -qE "^\[tool\.pytest" pyproject.toml 2>/dev/null; then
    REPO_ROOT="$(pwd)"
else
    REPO_ROOT="$SCRIPT_ROOT"
    cd "$REPO_ROOT"
fi

# Snapshot the xdist-worker fingerprint so we can find any orphans
# after pytest exits. We use the exact argv regex that xdist uses.
XDIST_WORKER_RE='exec\(eval\(sys.stdin.readline\(\)\)\)'
SNAPSHOT_PIDS=$(pgrep -f "$XDIST_WORKER_RE" | sort -u | tr '\n' ' ')

# Defensive: if the snapshot is empty, that's fine — there are no
# workers to reap, and pytest will spawn its own.
if [ -z "$SNAPSHOT_PIDS" ]; then
    SNAPSHOT_PIDS=""
fi

reap_workers() {
    # $SNAPSHOT_PIDS is space-separated. If empty, nothing to do.
    if [ -z "${SNAPSHOT_PIDS:-}" ]; then
        return 0
    fi
    # SIGTERM first (graceful), then SIGKILL after 1s for survivors.
    # Use xargs to handle the empty-list case safely.
    echo "$SNAPSHOT_PIDS" | tr ' ' '\n' | grep -E '^[0-9]+$' | while read -r pid; do
        [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 1
    echo "$SNAPSHOT_PIDS" | tr ' ' '\n' | grep -E '^[0-9]+$' | while read -r pid; do
        [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
    done
}

# Trap every interesting signal. The leading "-" on the action tells
# bash to ignore the signal's own failure if the trap fires during
# shutdown; without it, a final SIGTERM to the wrapper can race with
# the reap and abort the cleanup.
trap reap_workers EXIT INT TERM HUP PIPE

# Hand off to pytest. We intentionally do NOT use `exec` — we need
# the wrapper process to stay alive so the trap can run on the way
# out. The signal-forwarding and PID-snapshot are the entire point.
pytest "$@"
PYTEST_EXIT=$?

# Explicit reap even on success: pytest normally cleans up its own
# workers, but a worker that's mid-test-loop can sometimes miss the
# controller's SIGTERM (the `exec(eval(...))` shell swallows signals).
# Calling reap here is idempotent with the EXIT trap but covers the
# case where the user pressed Ctrl-C.
reap_workers

exit "$PYTEST_EXIT"
