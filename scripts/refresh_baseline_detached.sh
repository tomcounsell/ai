#!/usr/bin/env bash
#
# refresh_baseline_detached.sh — timeout-safe launcher for the merge-gate baseline refresh.
#
# WHY THIS EXISTS (issue #2066):
#   A full baseline refresh (`scripts/refresh_test_baseline.py`, 3 pytest passes) takes ~30 min
#   wall time on a quiesced machine, but the agent's foreground Bash tool caps at 10 minutes. A
#   foreground `python scripts/refresh_test_baseline.py` is therefore ALWAYS killed before it
#   finishes — the exact "refresh attempts die to the 10-min bash timeout" failure in #2066. This
#   wrapper launches the refresh detached (`nohup`), returns immediately with a PID + log path, and
#   captures the child's exit code across detachment so a failed/degraded run is never silent.
#
#   The concurrency / cross-worktree contention axis was already fixed by #2064 (machine-global
#   suite lock); `refresh_test_baseline.py` already serializes on that lock. This wrapper only adds
#   the detached-launch + exit-code-observability half.
#
# USAGE:
#   scripts/refresh_baseline_detached.sh [args forwarded to refresh_test_baseline.py]
#   scripts/refresh_baseline_detached.sh                 # default: --runs 3 against tests/
#   scripts/refresh_baseline_detached.sh --runs 5
#
# POLL FOR COMPLETION:
#   grep -E 'EXIT=|Wrote ' logs/baseline_refresh_<ts>.log
#     EXIT=0  -> fresh baseline written (data/main_test_baseline.json updated)
#     EXIT=1  -> FAILED (stale baseline left unchanged) OR DEGRADED (<2 usable runs, baseline
#                stamped degraded=true). Inspect the log to tell which.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Log + pidfile directory. Defaults to logs/; overridable via
# BASELINE_REFRESH_LOG_DIR so tests can isolate their own runs from a real
# in-flight refresh (and from each other).
LOG_DIR="${BASELINE_REFRESH_LOG_DIR:-$REPO_ROOT/logs}"
mkdir -p "$LOG_DIR"
PIDFILE="$LOG_DIR/baseline_refresh.pid"

# Concurrency guard: refuse to spawn a second refresh if one is already live.
# Prevents two launches from clobbering the pidfile and prevents a degraded
# second run from overwriting a clean first result (critique concern).
if [[ -f "$PIDFILE" ]]; then
    existing_pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
        echo "baseline refresh already running (PID $existing_pid);"
        echo "tail its log under $LOG_DIR/baseline_refresh_*.log — not launching a second run."
        exit 0
    fi
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/baseline_refresh_${TS}.log"

# Pre-create the log so the path we print always exists, even if a caller greps
# it in the sub-second window before the detached child's first write lands.
: >"$LOG"

# Launch detached. The inner shell runs the refresh, then appends an EXIT= line
# so a poller can distinguish success (EXIT=0) from failed/degraded (EXIT=1)
# even though this wrapper returned long before the child finished.
nohup bash -c '
    log="$1"; shift
    python scripts/refresh_test_baseline.py "$@" >>"$log" 2>&1
    echo "EXIT=$? at $(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$log"
' _ "$LOG" "$@" >/dev/null 2>&1 &

child_pid=$!
echo "$child_pid" >"$PIDFILE"

echo "baseline refresh launched detached:"
echo "  PID : $child_pid"
echo "  log : $LOG"
echo "poll: grep -E 'EXIT=|Wrote ' \"$LOG\"   # EXIT=0 fresh; EXIT=1 failed OR degraded (inspect log)"
