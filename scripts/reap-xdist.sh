#!/usr/bin/env bash
# reap-xdist: kill orphan pytest processes on the system.
#
# Covers two orphan classes:
#   1. xdist workers (python -c "import sys;exec(eval(...))") left behind
#      by interrupted parallel runs.
#   2. Serial pytest drivers (`pytest .../tests/`) whose parent shell
#      died — these are the dominant memory leak class on a machine
#      that runs interactive pytest through Claude Code. Each orphan
#      holds 100-200 MB of Python+import cache.
#
# Safety: every candidate is checked to ensure its parent is gone
# (PPID=1, parent not in the live process table, or parent is a
# short-lived /bin/zsh -c wrapper that has already exited). Pytest
# processes with a live parent (claude/zsh in a real session) are
# never touched.
#
# Usage:
#   scripts/reap-xdist.sh           # dry-run: print what would die
#   scripts/reap-xdist.sh --apply   # actually kill (TERM then KILL)
#
# Idempotent. Exits 0 either way.

set -euo pipefail

APPLY=0
for arg in "$@"; do
    case "$arg" in
        --apply) APPLY=1 ;;
        --help|-h)
            sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# --- find candidates -------------------------------------------------------

# xdist workers (parallel mode)
XDIST_RE='exec\(eval\(sys.stdin.readline\(\)\)'
# Serial pytest drivers: anything invoking pytest under our venv or a
# bare "pytest" command, but NOT the reaper itself or pytest-clean.sh.
PYTEST_RE='(pytest|pytest-xdist).*tests/'

# bash 3.2 (macOS /bin/bash) lacks mapfile; use a here-loop instead.
ALL_PIDS=()
while IFS= read -r pid; do
    [ -n "$pid" ] && ALL_PIDS+=("$pid")
done < <( { pgrep -f "$XDIST_RE" 2>/dev/null; pgrep -f "$PYTEST_RE" 2>/dev/null; } | grep -v "^$$\$" | grep -v "$(basename "$0")" | sort -u )

if [ "${#ALL_PIDS[@]}" -eq 0 ]; then
    echo "No orphan pytest processes."
    exit 0
fi

# --- filter: only kill if parent is dead -----------------------------------

ORPHAN_PIDS=()
LIVE_PARENT_PIDS=()
for pid in "${ALL_PIDS[@]}"; do
    [ -z "$pid" ] && continue
    # ps -o outputs: PID PPID STAT ETIME COMMAND
    read -r ppid stat etime _ < <(ps -p "$pid" -o pid=,ppid=,stat=,etime=,comm= 2>/dev/null | tr -s ' ' || echo "")
    if [ -z "$ppid" ]; then
        continue  # process vanished between pgrep and ps
    fi
    # PPID 1 (launchd adopted) = orphan by definition
    if [ "$ppid" = "1" ]; then
        ORPHAN_PIDS+=("$pid")
        continue
    fi
    # Parent still exists? Then keep this process — it's attached to
    # a live shell/session that may want it.
    if ps -p "$ppid" >/dev/null 2>&1; then
        LIVE_PARENT_PIDS+=("$pid")
        continue
    fi
    # Parent gone → orphan
    ORPHAN_PIDS+=("$pid")
done

if [ "${#ORPHAN_PIDS[@]}" -eq 0 ]; then
    echo "No orphan pytest processes (${#LIVE_PARENT_PIDS[@]} live-parent kept)."
    exit 0
fi

# --- report ---------------------------------------------------------------

RSS_TOTAL_KB=0
echo "Orphan pytest processes (${#ORPHAN_PIDS[@]}):"
for pid in "${ORPHAN_PIDS[@]}"; do
    rss_kb=$(ps -p "$pid" -o rss= 2>/dev/null | tr -d ' ' || echo 0)
    rss_kb=${rss_kb:-0}
    RSS_TOTAL_KB=$((RSS_TOTAL_KB + rss_kb))
    etime=$(ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ' || echo "?")
    cmd=$(ps -p "$pid" -o command= 2>/dev/null | head -c 80 || echo "")
    printf "  pid=%s  rss=%4d MB  etime=%-12s  %s\n" \
        "$pid" $((rss_kb / 1024)) "$etime" "$cmd"
done
RSS_TOTAL_MB=$((RSS_TOTAL_KB / 1024))
echo "Total RSS to reclaim: ${RSS_TOTAL_MB} MB"

if [ "$APPLY" -eq 0 ]; then
    echo "Dry-run. Re-run with --apply to kill."
    exit 0
fi

# --- kill (TERM, then KILL) ------------------------------------------------

echo "Sending SIGTERM..."
for pid in "${ORPHAN_PIDS[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
done
sleep 2

echo "Sending SIGKILL to survivors..."
for pid in "${ORPHAN_PIDS[@]}"; do
    if ps -p "$pid" >/dev/null 2>&1; then
        kill -KILL "$pid" 2>/dev/null || true
    fi
done

# --- recount survivors -----------------------------------------------------

REMAIN_PIDS=()
while IFS= read -r pid; do
    [ -n "$pid" ] && REMAIN_PIDS+=("$pid")
done < <( { pgrep -f "$XDIST_RE" 2>/dev/null; pgrep -f "$PYTEST_RE" 2>/dev/null; } | sort -u )
REMAINING=${#REMAIN_PIDS[@]}
echo "Done. Remaining pytest processes: $REMAINING"
exit 0
