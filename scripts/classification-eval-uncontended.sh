#!/bin/bash
# classification-eval-uncontended.sh: run one classifier comparison with the
# contended services unloaded, and put them back whatever happens (#3421).
#
# The comparison runner stamps a record `contended: true` when any of
# com.valor.bridge, com.valor.worker, or com.valor.reflection-worker is loaded
# (Race 3), and such a record cannot clear the bar. On the landing host those
# services carry live Telegram traffic, so every run is a bounded outage
# (Risk 8). This wrapper is the one sanctioned way to run a comparison there:
#
#   1. Refuse a thirteenth run in one UTC day (MAX_UNCONTENDED_RUNS_PER_DAY,
#      counter file data/classification_eval_runs.<YYYY-MM-DD>, no override)
#      and refuse to run beside another invocation (a lock directory under
#      RUNS_DIR, removed on exit) before touching any service.
#   2. Record which of the three labels `launchctl list` reports loaded, and
#      the bridge log's size, so the reconnect check below reads only lines
#      written after this point.
#   3. Install the EXIT trap, then unload exactly those labels
#      (`valor-service.sh stop`, `valor-service.sh worker-disable`,
#      `launchctl bootout gui/$(id -u)/com.valor.reflection-worker`).
#   4. Run `python -m tools.classification_eval "$@"` under RUN_TIMEOUT_S
#      (default 1800): a runner still going at the bound is sent SIGTERM so
#      the restore below runs; the run exits 124.
#   5. On every exit path (exit 0, a shortfall exit 2, a traceback, Ctrl-C,
#      the timeout) restore what was unloaded, each label with its own
#      command so one failure never skips the next: `valor-service.sh start`
#      for the bridge, `valor-service.sh worker-start` for the worker
#      (re-enables and starts it), `install_reflection_worker.sh` for the
#      reflection worker. Then, when the bridge was loaded, wait up to
#      BRIDGE_WAIT_S for a "Connected to Telegram" line appended to
#      logs/bridge.log since step 2. Exit 1 naming every service that failed
#      to return; otherwise the runner's exit code is propagated.
#
# Every external command is read through an environment variable with the
# real default, so tests/unit/test_classification_eval_uncontended.py drives
# this script against fakes:
#   VALOR_SERVICE_SH, INSTALL_REFLECTION_SH, LAUNCHCTL, CLASSIFICATION_EVAL_CMD
#   (a command line, split on whitespace), BRIDGE_LOG, RUNS_DIR, BRIDGE_WAIT_S,
#   RUN_TIMEOUT_S.
#
# Usage: scripts/classification-eval-uncontended.sh --site <id> --candidate <arms> [runner args]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

VALOR_SERVICE_SH="${VALOR_SERVICE_SH:-$PROJECT_DIR/scripts/valor-service.sh}"
INSTALL_REFLECTION_SH="${INSTALL_REFLECTION_SH:-$PROJECT_DIR/scripts/install_reflection_worker.sh}"
LAUNCHCTL="${LAUNCHCTL:-launchctl}"
CLASSIFICATION_EVAL_CMD="${CLASSIFICATION_EVAL_CMD:-$PROJECT_DIR/.venv/bin/python -m tools.classification_eval}"
BRIDGE_LOG="${BRIDGE_LOG:-$PROJECT_DIR/logs/bridge.log}"
RUNS_DIR="${RUNS_DIR:-$PROJECT_DIR/data}"
BRIDGE_WAIT_S="${BRIDGE_WAIT_S:-60}"
RUN_TIMEOUT_S="${RUN_TIMEOUT_S:-1800}"
MAX_UNCONTENDED_RUNS_PER_DAY=12

BRIDGE_LABEL="com.valor.bridge"
WORKER_LABEL="com.valor.worker"
REFLECTION_LABEL="com.valor.reflection-worker"
CONNECTED_MARKER="Connected to Telegram"

# --- 1. the daily bound and the lock, checked before any service is touched ---------

day="$(date -u +%Y-%m-%d)"
counter="$RUNS_DIR/classification_eval_runs.$day"
lock="$RUNS_DIR/.classification_eval_uncontended.lock"
timed_out_flag="$lock.timed_out"
mkdir -p "$RUNS_DIR"
runs_today=0
if [ -f "$counter" ]; then
    runs_today="$(tr -dc '0-9' < "$counter")"
    runs_today="${runs_today:-0}"
fi
if [ "$runs_today" -ge "$MAX_UNCONTENDED_RUNS_PER_DAY" ]; then
    echo "refusing: $runs_today uncontended runs already made on $day (UTC);" \
        "MAX_UNCONTENDED_RUNS_PER_DAY=$MAX_UNCONTENDED_RUNS_PER_DAY and there is no override." \
        "Nothing was stopped. Resume on the next UTC day." >&2
    exit 3
fi
# mkdir is atomic: a second wrapper started while the first holds the lock
# would see the services already unloaded, restore nothing, and run against
# a bridge the first's restore brings back mid-pass (contended: false on a
# live bridge). The refusing side has stopped nothing.
if ! mkdir "$lock" 2>/dev/null; then
    echo "refusing: another uncontended run holds $lock; wait for it to exit" \
        "(remove the directory only if no wrapper is running). Nothing was stopped." >&2
    exit 4
fi
rm -f "$timed_out_flag"
echo $((runs_today + 1)) > "$counter"

# --- 2. which contended labels are loaded right now, and where the bridge log ends --

loaded_labels="$("$LAUNCHCTL" list 2>/dev/null | awk -F'\t' '{print $NF}')"
is_loaded() {
    printf '%s\n' "$loaded_labels" | grep -qx "$1"
}
bridge_loaded=0
worker_loaded=0
reflection_loaded=0
is_loaded "$BRIDGE_LABEL" && bridge_loaded=1
is_loaded "$WORKER_LABEL" && worker_loaded=1
is_loaded "$REFLECTION_LABEL" && reflection_loaded=1
echo "uncontended run $((runs_today + 1))/$MAX_UNCONTENDED_RUNS_PER_DAY on $day:" \
    "bridge=$bridge_loaded worker=$worker_loaded reflection-worker=$reflection_loaded loaded"

# The marker is logged once per connect and the log rotates only when
# oversized, so a marker from before the stop sits in the tail for the whole
# run; only bytes appended after this offset count as the bridge coming back.
bridge_log_offset=0
if [ -f "$BRIDGE_LOG" ]; then
    bridge_log_offset="$(wc -c < "$BRIDGE_LOG" | tr -d ' ')"
fi

bridge_reconnected() {
    local size=0
    [ -f "$BRIDGE_LOG" ] && size="$(wc -c < "$BRIDGE_LOG" | tr -d ' ')"
    if [ "$size" -lt "$bridge_log_offset" ]; then
        # rotated or truncated since the stop: everything in it is new
        bridge_log_offset=0
    fi
    tail -c +"$((bridge_log_offset + 1))" "$BRIDGE_LOG" 2>/dev/null | grep -q "$CONNECTED_MARKER"
}

# --- 3. the restore, armed before anything is unloaded --------------------------------

runner_rc=1
restored=0
watchdog_pid=""

restore() {
    if [ "$restored" -eq 1 ]; then
        exit "$runner_rc"
    fi
    restored=1
    rmdir "$lock" 2>/dev/null
    if [ -n "$watchdog_pid" ]; then
        kill -TERM "$watchdog_pid" 2>/dev/null
        wait "$watchdog_pid" 2>/dev/null
    fi
    local rc="$runner_rc"
    local failed=""
    if [ "$bridge_loaded" -eq 1 ]; then
        "$VALOR_SERVICE_SH" start \
            || { echo "WARNING: $VALOR_SERVICE_SH start exited $?" >&2; failed="$failed bridge"; }
    fi
    if [ "$worker_loaded" -eq 1 ]; then
        "$VALOR_SERVICE_SH" worker-start \
            || { echo "WARNING: $VALOR_SERVICE_SH worker-start exited $?" >&2; failed="$failed worker"; }
    fi
    if [ "$reflection_loaded" -eq 1 ]; then
        "$INSTALL_REFLECTION_SH" \
            || { echo "WARNING: $INSTALL_REFLECTION_SH exited $?" >&2; failed="$failed reflection-worker"; }
    fi
    if [ "$bridge_loaded" -eq 1 ]; then
        local waited=0 connected=0
        while [ "$waited" -le "$BRIDGE_WAIT_S" ]; do
            if bridge_reconnected; then
                connected=1
                break
            fi
            sleep 1
            waited=$((waited + 1))
        done
        if [ "$connected" -eq 1 ]; then
            echo "bridge reconnected ($CONNECTED_MARKER)"
        else
            echo "ERROR: the bridge did not show '$CONNECTED_MARKER' in $BRIDGE_LOG" \
                "within ${BRIDGE_WAIT_S}s after restart; check $VALOR_SERVICE_SH status" >&2
            failed="$failed bridge(no-connect)"
        fi
    fi
    if [ -n "$failed" ]; then
        echo "ERROR: services that failed to return:$failed" >&2
        exit 1
    fi
    exit "$rc"
}

trap restore EXIT
# A signal before the runner starts becomes an exit, which runs the EXIT
# trap above; once the runner is in flight the traps below forward it first.
trap 'runner_rc=130; exit 130' INT
trap 'runner_rc=143; exit 143' TERM

# --- unload exactly what was loaded --------------------------------------------------

if [ "$bridge_loaded" -eq 1 ]; then
    "$VALOR_SERVICE_SH" stop || { echo "ERROR: $VALOR_SERVICE_SH stop exited $?" >&2; exit 1; }
fi
if [ "$worker_loaded" -eq 1 ]; then
    "$VALOR_SERVICE_SH" worker-disable \
        || { echo "ERROR: $VALOR_SERVICE_SH worker-disable exited $?" >&2; exit 1; }
fi
if [ "$reflection_loaded" -eq 1 ]; then
    "$LAUNCHCTL" bootout "gui/$(id -u)/$REFLECTION_LABEL" \
        || { echo "ERROR: $LAUNCHCTL bootout $REFLECTION_LABEL exited $?" >&2; exit 1; }
fi

# --- 4. the run, bounded by RUN_TIMEOUT_S ---------------------------------------------

read -r -a runner_cmd <<< "$CLASSIFICATION_EVAL_CMD"
cd "$PROJECT_DIR" || exit 1
# Under job control the runner keeps the default SIGINT disposition (a plain
# background job inherits SIGINT ignored, which a Python runner would keep,
# and Ctrl-C forwarded below would then do nothing) and runs in its own
# process group; the traps below are how a signal to the wrapper reaches it.
set -m
"${runner_cmd[@]}" "$@" &
runner_pid=$!
set +m
# The daily bound counts runs, not minutes: a wedged or crawling runner would
# otherwise keep the production bridge down until a human sent Ctrl-C. The
# watchdog sleeps in a child it can end, so cancelling it leaves no sleeper.
(
    sleeper=
    trap 'kill "$sleeper" 2>/dev/null; exit 0' TERM
    sleep "$RUN_TIMEOUT_S" &
    sleeper=$!
    wait "$sleeper"
    echo "ERROR: the runner exceeded RUN_TIMEOUT_S=${RUN_TIMEOUT_S}s; sending SIGTERM" >&2
    touch "$timed_out_flag"
    kill -TERM "$runner_pid" 2>/dev/null
    sleep 10 &
    sleeper=$!
    wait "$sleeper"
    kill -KILL "$runner_pid" 2>/dev/null
) &
watchdog_pid=$!
# Ctrl-C and SIGTERM go to the runner first; the wrapper waits for it to
# leave and then exits through restore, so the services come back after the
# runner is gone rather than beside it.
trap 'kill -INT "$runner_pid" 2>/dev/null; wait "$runner_pid"; runner_rc=130; exit 130' INT
trap 'kill -TERM "$runner_pid" 2>/dev/null; wait "$runner_pid"; runner_rc=143; exit 143' TERM
wait "$runner_pid"
runner_rc=$?
# restore ends the watchdog; the flag it wrote before the SIGTERM is the
# only sign the bound fired (the runner's own status is the signal's).
if [ -f "$timed_out_flag" ]; then
    rm -f "$timed_out_flag"
    runner_rc=124
fi
exit "$runner_rc"
