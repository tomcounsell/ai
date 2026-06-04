#!/usr/bin/env bash
# reap-xdist: kill any orphan xdist pytest workers on the system.
#
# Use this when an interrupted pytest run (timeout, agent shell exit,
# Ctrl-C) has left `python -c "import sys;exec(eval(...))"` workers
# behind. Idempotent: prints a count and exits 0 either way.
#
# Run after every interactive pytest invocation if you don't use
# scripts/pytest-clean.sh.
#
# Exit codes:
#   0 - no orphans remain (whether we killed any or there were none)
#   1 - something went wrong (e.g., pgrep unavailable)

set -euo pipefail

XDIST_WORKER_RE='exec\(eval\(sys.stdin.readline\(\)\)'
# Two-step: TERM, then KILL survivors. xdist workers trap SIGTERM
# gracefully but the exec(eval) wrapper sometimes swallows the trap.
PIDS=$(pgrep -f "$XDIST_WORKER_RE" | sort -u || true)
if [ -z "$PIDS" ]; then
    echo "No orphan xdist workers."
    exit 0
fi

COUNT=$(echo "$PIDS" | wc -l | tr -d ' ')
echo "Reaping $COUNT orphan xdist worker(s)..."
echo "$PIDS" | while read -r pid; do
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
done
sleep 1
echo "$PIDS" | while read -r pid; do
    [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
done

REMAINING=$(pgrep -f "$XDIST_WORKER_RE" | wc -l | tr -d ' ' || echo 0)
echo "Done. Remaining workers: $REMAINING"
exit 0
