# Shared ancestor-safe service-PID helper for shell probes (#3187 item 2, #3265).
#
# BSD `pgrep` — the one macOS ships — excludes the calling process AND its whole
# ancestor chain from the match list. Every agent session here is a `claude -p`
# descendant of the worker (and historically of the bridge), so a `pgrep`
# liveness probe run from inside a session reads its own live host service as
# absent. `./scripts/valor-service.sh worker-status` printing STOPPED against a
# worker with 21h uptime is the canonical symptom (#3265).
#
# `-a` is not the fix: on Linux/procps `-a` means "print the full command line",
# so the flag silently changes meaning off macOS.
#
# The Python probes were converted under #3164 to `tools/process_lookup.py`,
# which reads `ps` (no ancestor filter) and parses each command line as a
# CPython invocation. This file is how shell reaches that SAME module — it is a
# transport, not a second implementation. Nothing here parses a process table.
#
# Contract, chosen so a converted call site keeps its existing shape:
#
#   service_pids --module worker --script-suffix worker/__main__.py
#       → matching PIDs on stdout, one per line; exit 0 if any, 1 if none.
#         Identical to `pgrep -f` minus the ancestor exclusion.
#
#   service_pid_is_own_ancestor <pid>
#       → exit 0 if <pid> is an ancestor of this shell. Kill paths MUST gate on
#         this. `pgrep` made caller-fratricide unreachable by accident; an
#         ancestor-safe lookup makes it an explicit decision, and the answer
#         fails closed (anything short of a definitive "no" reports "yes,
#         ancestor") so a probe failure can never escalate into signalling our
#         own host service.
#
# Sourcing this file requires SCRIPT_DIR or PROJECT_DIR to already be set by the
# sourcing script; both `valor-service.sh` and `start_bridge.sh` set them at the
# top, before any probe runs.
#
# Test isolation: shell harnesses used to shadow `pgrep` on PATH so a sandboxed
# run could not read — or `kill` against — the real host process table. They now
# install a stub *of this file* into their fake project's `scripts/lib/`, which
# is a strictly tighter seam: it intercepts every probe by name rather than
# hoping the real binary is not reached by some other route.

# Resolve once, at source time, so every probe in a run agrees on an
# interpreter. `tools/process_lookup.py` is stdlib-only by design, so a bare
# `python3` is a correct fallback on a host whose virtualenv is missing or
# half-built — which is exactly the state `start_bridge.sh` probes in.
_SERVICE_PIDS_ROOT="${PROJECT_DIR:-$(dirname "${SCRIPT_DIR:-$(dirname "${BASH_SOURCE[0]}")}")}"
if [ -x "$_SERVICE_PIDS_ROOT/.venv/bin/python" ]; then
    _SERVICE_PIDS_PYTHON="$_SERVICE_PIDS_ROOT/.venv/bin/python"
else
    _SERVICE_PIDS_PYTHON="$(command -v python3 || echo python3)"
fi

# PYTHONPATH is pinned to the checkout that owns this file so a probe run from
# any cwd — a worktree, a cron with no cwd, `cd /` — resolves the same module.
service_pids() {
    PYTHONPATH="$_SERVICE_PIDS_ROOT" "$_SERVICE_PIDS_PYTHON" \
        -m tools.process_lookup "$@" 2>/dev/null
}

# Only exit 1 — the CLI's definitive "walked to init, no match" — counts as "not
# an ancestor". Every other exit is inconclusive and answers "yes, ancestor":
# argparse rejecting a malformed PID token (2), a missing interpreter (127), an
# import failure. Mapping those to "no" would fail OPEN in exactly the case the
# guard exists for, which is how a probe failure turns into signalling our own
# host service.
service_pid_is_own_ancestor() {
    PYTHONPATH="$_SERVICE_PIDS_ROOT" "$_SERVICE_PIDS_PYTHON" \
        -m tools.process_lookup --is-own-ancestor "$1" 2>/dev/null
    [ "$?" -eq 1 ] && return 1
    return 0
}

# Gate every `kill` that acts on a PID from this file. Exit 1 (and explain) when
# the target is the caller's own ancestor.
#
# This refusal is the cost of the ancestor-safe lookup, and it is deliberate.
# `pgrep` used to make caller-fratricide unreachable by accident: it hid the
# host service, so `stop_worker` printed "Worker is not running" and did
# nothing. That was a lie, but a harmless one. Now the lookup returns the real
# PID, and `kill -9` on it would take down the very session running the command
# — mid-command, so the operator gets no completion, no exit status, and a
# half-finished stop. Refusing with instructions is the only outcome that is
# both truthful and survivable.
#
# $1 = one or more whitespace-separated PIDs, $2 = human-readable service name,
# $3 = launchctl-based alternative.
#
# $1 is deliberately a LIST, not a single PID: every selector below can return
# more than one (a double-bridge is precisely the state `start_bridge.sh` is
# cleaning up), and each of those PIDs is about to be signalled. Checking only
# the first would let a caller kill its own ancestor whenever the ancestor is
# not the lowest-numbered match. Unquoted expansion is what splits the list.
service_pid_refuse_self_kill() {
    local pids="$1" name="$2" alternative="$3" pid
    for pid in $pids; do
        if service_pid_is_own_ancestor "$pid"; then
            echo "REFUSING to stop $name (PID: $pid): it is an ancestor of this process."
            echo "  This command is running inside a session hosted by that $name, so"
            echo "  killing it would terminate this command before it could finish."
            echo "  Run it from a shell outside the service, or use: $alternative"
            return 1
        fi
    done
    return 0
}

# Selectors for the three long-lived services, defined once so a call site can
# never drift from the shape the launchd plists actually spawn.
service_pids_worker() {
    service_pids --module worker --script-suffix worker/__main__.py
}

service_pids_bridge() {
    service_pids --script-suffix bridge/telegram_bridge.py
}

service_pids_email() {
    service_pids --module bridge.email_bridge
}
