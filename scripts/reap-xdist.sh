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
# never touched — UNLESS that parent is itself an orphaned (PPID==1)
# `sh -c`/`zsh -c` wrapper (issue #1632): such a wrapper is alive only
# because it is blocked waiting on its child forever, so the chain
# above the candidate is dead and the candidate is an orphan.
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
# Each pgrep needs `|| true`: the process substitution inherits `set -e`,
# so a no-match first pgrep (exit 1) would abort the group and silently
# skip the serial-pytest scan entirely (issue #1632 — this is why the
# reaper reported "No orphan pytest processes" while 7 existed).
ALL_PIDS=()
while IFS= read -r pid; do
    [ -n "$pid" ] && ALL_PIDS+=("$pid")
done < <( { pgrep -f "$XDIST_RE" 2>/dev/null || true; pgrep -f "$PYTEST_RE" 2>/dev/null || true; } | grep -v "^$$\$" | grep -v "$(basename "$0")" | sort -u )

if [ "${#ALL_PIDS[@]}" -eq 0 ]; then
    echo "No orphan pytest processes."
    exit 0
fi

# --- filter: only kill if parent is dead -----------------------------------

# Dead-chain check (issue #1632): return 0 if PID $1 is a `sh -c`-style
# shell wrapper (sh/zsh/bash/dash, `-c` as an early arg) whose own PPID==1.
# Such a wrapper survived its session's death only because it is blocked
# waiting on its child; the pytest under it is effectively an orphan.
# Any ps failure returns 1 (keep the candidate — conservative default).
parent_is_orphaned_shell_wrapper() {
    local wpid="$1"
    local gppid pcmd shell_word
    gppid=$({ ps -p "$wpid" -o ppid= 2>/dev/null || true; } | tr -d ' ')
    [ "$gppid" = "1" ] || return 1
    pcmd=$(ps -p "$wpid" -o command= 2>/dev/null || true)
    [ -n "$pcmd" ] || return 1
    shell_word=${pcmd%% *}          # first token of the command line
    shell_word=${shell_word##*/}    # basename (handles /bin/zsh)
    case "$shell_word" in
        sh|zsh|bash|dash) ;;
        *) return 1 ;;
    esac
    case "$pcmd" in
        *" -c "*) return 0 ;;
        *) return 1 ;;
    esac
}

ORPHAN_PIDS=()
LIVE_PARENT_PIDS=()
for pid in "${ALL_PIDS[@]}"; do
    [ -z "$pid" ] && continue
    # Fetch ONLY the ppid. (Issue #1632: the previous multi-column read
    # `read -r ppid stat etime _` consumed the PID column into $ppid, so the
    # orphan filter compared each candidate's own pid to 1 and probed its own
    # liveness — every candidate was misclassified as live-parent.)
    ppid=$({ ps -p "$pid" -o ppid= 2>/dev/null || true; } | tr -d ' ')
    if [ -z "$ppid" ]; then
        continue  # process vanished between pgrep and ps
    fi
    # PPID 1 (launchd adopted) = orphan by definition
    if [ "$ppid" = "1" ]; then
        ORPHAN_PIDS+=("$pid")
        continue
    fi
    # Parent still exists? Keep this process — it's attached to a live
    # shell/session that may want it — UNLESS the parent is itself an
    # orphaned (PPID==1) `sh -c` wrapper: that chain is dead (issue #1632).
    if ps -p "$ppid" >/dev/null 2>&1; then
        if parent_is_orphaned_shell_wrapper "$ppid"; then
            ORPHAN_PIDS+=("$pid")
        else
            LIVE_PARENT_PIDS+=("$pid")
        fi
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
done < <( { pgrep -f "$XDIST_RE" 2>/dev/null || true; pgrep -f "$PYTEST_RE" 2>/dev/null || true; } | sort -u )
REMAINING=${#REMAIN_PIDS[@]}
echo "Done. Remaining pytest processes: $REMAINING"
exit 0
