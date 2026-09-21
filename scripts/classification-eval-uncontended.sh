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
#      before touching any service.
#   2. Record which of the three labels `launchctl list` reports loaded.
#   3. Install the EXIT trap, then unload exactly those labels
#      (`valor-service.sh stop`, `valor-service.sh worker-disable`,
#      `launchctl bootout gui/$(id -u)/com.valor.reflection-worker`).
#   4. Run `python -m tools.classification_eval "$@"`.
#   5. On every exit path (exit 0, a shortfall exit 2, a traceback, Ctrl-C)
#      restore what was unloaded: `valor-service.sh restart` when the bridge
#      or the worker was loaded (restart re-enables and starts the worker),
#      `install_reflection_worker.sh` when the reflection worker was loaded;
#      then, when the bridge was loaded, wait up to BRIDGE_WAIT_S for
#      `tail -5 logs/bridge.log` to show "Connected to Telegram" and exit
#      non-zero naming the bridge when it does not. Otherwise the runner's
#      exit code is propagated.
#
# Every external command is read through an environment variable with the
# real default, so tests/unit/test_classification_eval_uncontended.py drives
# this script against fakes:
#   VALOR_SERVICE_SH, INSTALL_REFLECTION_SH, LAUNCHCTL, CLASSIFICATION_EVAL_CMD
#   (a command line, split on whitespace), BRIDGE_LOG, RUNS_DIR, BRIDGE_WAIT_S.
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
MAX_UNCONTENDED_RUNS_PER_DAY=12

BRIDGE_LABEL="com.valor.bridge"
WORKER_LABEL="com.valor.worker"
REFLECTION_LABEL="com.valor.reflection-worker"
CONNECTED_MARKER="Connected to Telegram"

# --- 1. the daily bound, checked before any service is touched ---------------------

day="$(date -u +%Y-%m-%d)"
counter="$RUNS_DIR/classification_eval_runs.$day"
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
echo $((runs_today + 1)) > "$counter"

# --- 2. which contended labels are loaded right now ----------------------------------

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

# --- 3. the restore, armed before anything is unloaded --------------------------------

runner_rc=1
restored=0

restore() {
    if [ "$restored" -eq 1 ]; then
        exit "$runner_rc"
    fi
    restored=1
    local rc="$runner_rc"
    if [ "$bridge_loaded" -eq 1 ] || [ "$worker_loaded" -eq 1 ]; then
        "$VALOR_SERVICE_SH" restart || echo "WARNING: $VALOR_SERVICE_SH restart exited $?" >&2
    fi
    if [ "$reflection_loaded" -eq 1 ]; then
        "$INSTALL_REFLECTION_SH" || echo "WARNING: $INSTALL_REFLECTION_SH exited $?" >&2
    fi
    if [ "$bridge_loaded" -eq 1 ]; then
        local waited=0
        until tail -5 "$BRIDGE_LOG" 2>/dev/null | grep -q "$CONNECTED_MARKER"; do
            if [ "$waited" -ge "$BRIDGE_WAIT_S" ]; then
                echo "ERROR: the bridge did not show '$CONNECTED_MARKER' in $BRIDGE_LOG" \
                    "within ${BRIDGE_WAIT_S}s after restart; check $VALOR_SERVICE_SH status" >&2
                exit 1
            fi
            sleep 1
            waited=$((waited + 1))
        done
        echo "bridge reconnected ($CONNECTED_MARKER)"
    fi
    exit "$rc"
}

trap restore EXIT
# A signal while the runner is in flight is deferred until it exits; these
# turn it into an exit, which runs the EXIT trap above.
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

# --- 4. the run ----------------------------------------------------------------------

read -r -a runner_cmd <<< "$CLASSIFICATION_EVAL_CMD"
cd "$PROJECT_DIR" || exit 1
"${runner_cmd[@]}" "$@"
runner_rc=$?
exit "$runner_rc"
