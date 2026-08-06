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
#   1. Reaps any pre-existing xdist orphans BEFORE pytest starts (a
#      prior crash may have left workers behind).
#   2. Runs pytest under a trap that reaps any xdist workers we see
#      on EXIT, INT, TERM, HUP, PIPE. We re-snapshot at reap time
#      rather than trusting the cached PID list, because fresh
#      orphans may appear and stale PIDs may already be dead.
#   3. Honors the caller's cwd (worktree agents test the worktree).
#
# Usage:
#   scripts/pytest-clean.sh tests/unit/session_runner/
#   scripts/pytest-clean.sh -k "test_pick" tests/unit/
#   scripts/pytest-clean.sh -x   # all args pass through to pytest
#
# For an ad-hoc reaper (no test run), use scripts/reap-xdist.sh.

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

XDIST_WORKER_RE='exec\(eval\(sys\.stdin\.readline\(\)\)'

# On a shared machine two pytest runs can be live at once; a
# machine-wide reap from one run kills the other run's workers (mass
# "node down: Not properly terminated"). Only reap workers this wrapper
# owns (our PID is in the worker's ancestry) or true orphans (direct
# PPID 1 — their controller is gone). scripts/reap-xdist.sh remains the
# deliberate machine-wide sweep.
ours_or_orphan() {
    local pid="$1" current="$1" parent depth=0
    while [ "$depth" -lt 32 ]; do
        parent=$(ps -o ppid= -p "$current" 2>/dev/null | tr -d ' ')
        [ -z "$parent" ] && return 1
        if [ "$current" = "$pid" ] && [ "$parent" = "1" ]; then
            return 0  # orphaned worker, controller already gone
        fi
        [ "$parent" = "$$" ] && return 0
        [ "$parent" -le 1 ] 2>/dev/null && return 1
        current="$parent"
        depth=$((depth + 1))
    done
    return 1
}

reap_workers() {
    # Re-snapshot at reap time. The cached list (if any) is stale by
    # the time the trap fires; the live list is what we want.
    local now_pids own_pids pid
    now_pids=$(pgrep -f "$XDIST_WORKER_RE" 2>/dev/null | sort -u | tr '\n' ' ' || true)
    [ -z "$now_pids" ] && return 0
    own_pids=""
    for pid in $now_pids; do
        echo "$pid" | grep -qE '^[0-9]+$' || continue
        ours_or_orphan "$pid" && own_pids="$own_pids $pid"
    done
    [ -z "${own_pids// /}" ] && return 0
    for pid in $own_pids; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 1
    for pid in $own_pids; do
        kill -KILL "$pid" 2>/dev/null || true
    done
}

# The full-suite advisory lock (#1967) is DELETED, not disabled.
#
# It decided full-suite-ness from the pytest args, so any run naming a path
# below tests/ skipped it entirely. A full run therefore held a lock that no
# targeted run ever tried to acquire — the serialization it advertised did not
# exist for most runs, while its silence read as protection (#2535 Problem 1).
# It also made a full run wait up to 1800s behind another.
#
# A guard that cannot fail proves nothing, and one that fails open while looking
# closed is worse than none: it cost real agent-hours here chasing corrupted
# results that the lock implied were impossible.
#
# Real isolation belongs in key namespacing, not in serializing the machine.

cleanup() {
    reap_workers
}

# Trap every interesting signal. The leading "-" on the action tells
# bash to ignore the signal's own failure if the trap fires during
# shutdown; without it, a final SIGTERM to the wrapper can race with
# the reap and abort the cleanup.
trap cleanup EXIT INT TERM HUP PIPE

# Reap pre-existing orphans first. A prior crash may have left
# workers behind; pytest would spawn its own fresh set on top and
# we'd be in worse shape than before.
reap_workers

# Defense-in-depth against __pycache__ cross-checkout poisoning (issue #2064):
# don't write .pyc files during a suite run. The machine-global lock already
# serializes full-suite runs so concurrent poisoning can't happen, and each
# worktree has its own __pycache__ dir — this is cheap belt-and-suspenders
# against any future cross-checkout bytecode sharing. Scoped to the pytest
# subprocess (and its xdist workers) via export.
export PYTHONDONTWRITEBYTECODE=1

# Resolve the interpreter that owns this repo's dependencies. A bare `pytest`
# resolves from PATH, which on a machine with a user-site pytest picks an
# interpreter that never had `pip install -e .` run against it — so
# pytest-xdist is absent and pyproject's `-n auto --dist=loadfile` addopts
# abort the run with "unrecognized arguments" before a single test executes.
# The repo venv is the source of truth when it exists; PATH is the fallback
# for callers who have already activated an environment.
PYTEST_BIN="pytest"
if [ -x "$REPO_ROOT/.venv/bin/pytest" ]; then
    PYTEST_BIN="$REPO_ROOT/.venv/bin/pytest"
fi

# Fail loudly rather than mid-run if the resolved interpreter is missing xdist:
# the addopts are non-negotiable, so a missing plugin is a broken environment,
# not something to silently degrade around.
if ! "$PYTEST_BIN" --version >/dev/null 2>&1; then
    echo "pytest-clean: no usable pytest found (tried $PYTEST_BIN)" >&2
    echo "  Run \`uv sync\` or \`pip install -e .\` to populate .venv." >&2
    exit 1
fi

# Hand off to pytest. We intentionally do NOT use `exec` — we need
# the wrapper process to stay alive so the trap can run on the way
# out. The signal-forwarding and PID-snapshot are the entire point.
"$PYTEST_BIN" "$@"
PYTEST_EXIT=$?

# Explicit reap even on success: pytest normally cleans up its own
# workers, but a worker that's mid-test-loop can sometimes miss the
# controller's SIGTERM (the `exec(eval(...))` shell swallows signals).
# Calling reap here is idempotent with the EXIT trap but covers the
# case where the user pressed Ctrl-C.
reap_workers

exit "$PYTEST_EXIT"
