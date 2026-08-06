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

# Wedge detector (#2574). When an xdist worker dies with "node down: Not
# properly terminated", the controller can stop making progress entirely: it
# sits at 0% CPU forever, never emits the `-rf` summary, and has to be killed
# by pid. A run that stalls silently is the same defect class the per-test
# timeout was added to fix — the instrument fails without saying so, and the
# operator cannot tell a wedge from a slow suite.
#
# The signal is cumulative CPU time, sampled from `ps`. A live controller is
# constantly collecting results from its workers and accrues CPU steadily; a
# wedged one accrues essentially none. `--timeout=420` already bounds any
# single test, so a window this long without meaningful CPU cannot be a test
# legitimately blocking — it can only be a stall in collection, teardown, or
# sessionfinish, which is exactly where the per-test timeout cannot reach.
#
# The comparison is a DELTA against a threshold, not string equality on the
# `ps` output. A wedged controller is not perfectly frozen: an observed wedge
# accrued 0.03s of CPU over ten minutes, enough to change the hundredths field
# and reset an equality-based counter forever. Measured separation is wide —
# 0.03s wedged versus tens of seconds live over the same window — so a 1s
# floor discriminates cleanly.
#
# The limit is the low-CPU window, measured across 30s samples, so the
# detector fires a sample or two after it elapses rather than exactly on it.
# Set PYTEST_STALL_LIMIT_S=0 to disable (e.g. when attaching a debugger).
PYTEST_STALL_LIMIT_S="${PYTEST_STALL_LIMIT_S:-600}"
STALL_SAMPLE_S=30
STALL_CPU_EPSILON_S=1

# Cumulative CPU seconds for a pid. `ps -o time=` prints [DD-][HH:]MM:SS[.CC],
# so fields are summed right-to-left with 1/60/3600/86400 multipliers.
cpu_seconds() {
    ps -o time= -p "$1" 2>/dev/null | tr -d ' ' | awk -F'[:-]' '
        NF == 0 { exit 1 }
        {
            s = 0; m = 1
            for (i = NF; i >= 1; i--) {
                s += $i * m
                m = (m == 1) ? 60 : (m == 60) ? 3600 : 86400
            }
            printf "%.2f", s
        }'
}

watch_for_stall() {
    local pid="$1" mark="" stalled=0 cpu
    while kill -0 "$pid" 2>/dev/null; do
        sleep "$STALL_SAMPLE_S"
        kill -0 "$pid" 2>/dev/null || return 0
        cpu=$(cpu_seconds "$pid")
        [ -z "$cpu" ] && return 0
        if [ -z "$mark" ] || awk -v a="$cpu" -v b="$mark" -v e="$STALL_CPU_EPSILON_S" \
            'BEGIN { exit !(a - b >= e) }'; then
            stalled=0
            mark="$cpu"
        else
            stalled=$((stalled + STALL_SAMPLE_S))
        fi
        if [ "$stalled" -ge "$PYTEST_STALL_LIMIT_S" ]; then
            echo "" >&2
            echo "pytest-clean: WEDGED — the pytest controller (pid $pid) accrued under" >&2
            echo "  ${STALL_CPU_EPSILON_S}s of CPU in ${stalled}s. Terminating, so this run fails loudly with" >&2
            echo "  a non-zero status instead of sitting here forever with no summary (#2574)." >&2
            echo "" >&2
            echo "  Known cause (#2574): a test leaves a task that never answers" >&2
            echo "  cancellation, its event-loop teardown hangs, the per-test timeout" >&2
            echo "  fires and calls os._exit, and that kills the xdist worker outright" >&2
            echo "  ('node down: Not properly terminated'). Replacement workers die on" >&2
            echo "  the same test until the controller stops making progress." >&2
            echo "" >&2
            echo "  Two things to know before investigating:" >&2
            echo "    * Re-run with -v. A wedge emits no -rf summary, so without -v the" >&2
            echo "      run yields no failure list at all." >&2
            echo "    * TRUST the reported failing node, and re-run it alone with -n 0." >&2
            echo "      Serially the per-test timeout prints the hung stack instead of" >&2
            echo "      killing a worker, which names the leaked task directly." >&2
            echo "" >&2
            kill -TERM "$pid" 2>/dev/null || true
            sleep 10
            kill -KILL "$pid" 2>/dev/null || true
            return 0
        fi
    done
}

# Hand off to pytest. We intentionally do NOT use `exec` — we need
# the wrapper process to stay alive so the trap can run on the way
# out. The signal-forwarding and PID-snapshot are the entire point.
"$PYTEST_BIN" "$@" &
PYTEST_PID=$!

STALL_WATCHER_PID=""
if [ "$PYTEST_STALL_LIMIT_S" -gt 0 ] 2>/dev/null; then
    watch_for_stall "$PYTEST_PID" &
    STALL_WATCHER_PID=$!
fi

wait "$PYTEST_PID"
PYTEST_EXIT=$?

if [ -n "$STALL_WATCHER_PID" ]; then
    kill "$STALL_WATCHER_PID" 2>/dev/null || true
    wait "$STALL_WATCHER_PID" 2>/dev/null || true
fi

# Explicit reap even on success: pytest normally cleans up its own
# workers, but a worker that's mid-test-loop can sometimes miss the
# controller's SIGTERM (the `exec(eval(...))` shell swallows signals).
# Calling reap here is idempotent with the EXIT trap but covers the
# case where the user pressed Ctrl-C.
reap_workers

exit "$PYTEST_EXIT"
