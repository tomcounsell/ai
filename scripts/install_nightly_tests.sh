#!/bin/bash
# Install the nightly regression test launchd service (runs daily at 03:00).
# Usage: ./scripts/install_nightly_tests.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/launchctl.sh"

set -a
# shellcheck disable=SC1091
[ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
set +a
: "${SERVICE_LABEL_PREFIX:=com.valor}"

PLIST_SRC="$PROJECT_DIR/com.valor.nightly-tests.plist"
LABEL="${SERVICE_LABEL_PREFIX}.nightly-tests"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"

# ── Worktree refusal (issue #2823) ──────────────────────────────────────
# The plist is machine-global and hardcodes an absolute PROJECT_DIR. Installing
# from a lane worktree would aim the fleet's nightly detector at a directory
# that `/do-build` cleanup deletes once the lane's PR merges. Refuse before the
# role gate — a worktree's `.git` is a FILE containing `gitdir: <main-repo>/
# .git/worktrees/<slug>`, never a directory.
if [ -f "$PROJECT_DIR/.git" ] && grep -qE '^gitdir:.*/.git/worktrees/' "$PROJECT_DIR/.git" 2>/dev/null; then
    echo "Skipping nightly-tests install: running from a worktree checkout ($PROJECT_DIR)"
    echo "The nightly detector must only be installed from a machine's main checkout."
    exit 0
fi
# ── End worktree refusal ─────────────────────────────────────────────────

# ── Install gate: INSTALL-ONLY, no removal path ───────────────────────
#
# This gate answers one question — "does this host own a project?" — and its
# only possible actions are "install" or "do nothing". There is deliberately NO
# stale-plist removal here.
#
# That absence is the design, not an omission. The removal branch was reviewed
# four times and produced four bugs, and every one wrongly UNINSTALLED a
# healthy detector while not one caused a bad install:
#
#   1. `-eq 2` enumerated the exit codes meaning "undeterminable", missing the rest.
#   2. `-ne 0 && -ne 1` widened the enumeration and still missed exit 1 — what an
#      orphaned CPython and an unhandled heredoc exception both return, the same
#      1 that otherwise means "owns nothing".
#   3. `scutil` exiting 0 with EMPTY output produced a confident "owns nothing"
#      from an identity never established as usable.
#   4. A verdict printed and then followed by a crash was still trusted.
#
# The asymmetry is structural. Removal must be certain of a NEGATIVE ("this host
# owns nothing"), and every indeterminate input met so far collapsed into that
# confident negative. Installing needs certainty about a POSITIVE, which is far
# easier to establish and cheaper to get wrong: a spurious install is
# recoverable, a spurious uninstall runs silently across the fleet.
#
# Accepted cost: a machine that stops owning projects keeps a stale plist until
# a follow-up lands removal deliberately. Non-destructive and recoverable — the
# trade this gate makes on purpose.
#
# Properties covered by tests/integration/test_install_nightly_tests.py:
#   - The verdict is a printed TOKEN, never an exit code. An exit code the
#     interpreter did not choose cannot carry an answer.
#   - stderr is discarded, so a traceback can never read as a verdict.
#   - Anything other than an unambiguous ROLE:OWNS means DO NOTHING.
PROJECTS_CONFIG="${PROJECTS_CONFIG_PATH:-$HOME/Desktop/Valor/projects.json}"

owns_a_project() {
    [ -f "$PROJECTS_CONFIG" ] || return 1
    [ -x "$PROJECT_DIR/.venv/bin/python" ] || return 1
    "$PROJECT_DIR/.venv/bin/python" - "$PROJECTS_CONFIG" <<'PYEOF'
import json, subprocess, sys

try:
    host = subprocess.check_output(
        ["scutil", "--get", "ComputerName"], text=True
    ).strip()
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
except Exception:
    sys.exit(1)  # Print no token; the caller then does nothing.

# Reading an input is not the same as establishing it is meaningful. `scutil`
# can exit 0 printing NOTHING, which means "I don't know who I am".
#
# Under install-only this guard is BEHAVIOURALLY REDUNDANT and deliberately
# untested: an empty identity matches no project, so without it the result is
# ROLE:NONE, and ROLE:NONE now means "do nothing" — the same outcome. Mutation
# confirms removing it changes no test.
#
# It is kept because it stops being redundant the moment a removal path
# returns. This exact input produced a confident "owns nothing" that deleted a
# healthy detector's plist, and whoever implements removal (see the follow-up
# issue) needs the identity validated before a negative can be trusted.
if not host:
    sys.exit(1)

target = host.lower()
# `isinstance(proj, dict)` is likewise redundant here — a malformed entry would
# raise, the except above would exit without a token, and the caller would do
# nothing, which is already correct. Kept for the same reason: under a removal
# path, "it raised" must never be reachable as "owns nothing".
owns = any(
    (proj.get("machine") or "").strip().lower() == target
    for proj in (cfg.get("projects") or {}).values()
    if isinstance(proj, dict) and (proj.get("machine") or "").strip()
)
print("ROLE:OWNS" if owns else "ROLE:NONE")
PYEOF
}

role_out="$(owns_a_project 2>/dev/null || true)"

case "$role_out" in
    *ROLE:OWNS*)
        # Both tokens present means something other than the heredoc wrote to
        # stdout, so it is not an answer either.
        case "$role_out" in
            *ROLE:NONE*)
                echo "Skipping nightly-tests install: contradictory role output"
                echo "Not installing. No existing plist is touched."
                exit 0
                ;;
        esac
        ;;
    *)
        echo "Skipping nightly-tests install: this host does not own a project,"
        echo "or its role could not be determined. Not installing."
        echo "Any existing plist is left alone (removal is not this script's job)."
        exit 0
        ;;
esac
# ── End install gate ─────────────────────────────────────

# Prerequisite: pytest-json-report must be installed
if ! "$PROJECT_DIR/.venv/bin/python" -m pytest --json-report --help > /dev/null 2>&1; then
    echo "ERROR: pytest-json-report not installed. Run: uv pip install pytest-json-report"
    exit 1
fi

# Ensure logs directory exists
mkdir -p "$PROJECT_DIR/logs"

# Check source plist exists
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: Plist not found at $PLIST_SRC"
    exit 1
fi

# Unload existing version if present
if launchctl list | grep -q "$LABEL"; then
    echo "Unloading existing $LABEL..."
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

# Copy plist to LaunchAgents with path substitution
echo "Installing plist to $PLIST_DST..."
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__HOME_DIR__|$HOME|g; s|__SERVICE_LABEL__|$LABEL|g" "$PLIST_SRC" > "$PLIST_DST"

# Load new version
echo "Loading $LABEL..."
launchctl_bootstrap_fail_soft "gui/$(id -u)" "$PLIST_DST" "$LABEL" || exit 1

echo ""
echo "Nightly regression test service installed successfully."
echo "Label:    $LABEL"
echo "Schedule: daily at 03:00 local time"
echo "Log:      $PROJECT_DIR/logs/nightly_tests.log"
echo "Errors:   $PROJECT_DIR/logs/nightly_tests_error.log"
echo ""
echo "To run manually: python scripts/nightly_regression_tests.py --dry-run"
echo "To uninstall:    launchctl bootout gui/$(id -u)/$LABEL && rm $PLIST_DST"
