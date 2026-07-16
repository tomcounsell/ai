#!/bin/bash
# Install the email bridge as a launchd service for boot-time auto-start.
# Usage: ./scripts/install_email_bridge.sh
#
# The email bridge polls IMAP for inbound mail and runs the SMTP outbox relay.
# This installer is machine-gated: it only installs the launchd plist on
# machines that own at least one project with an `email:` block in
# ~/Desktop/Valor/projects.json. On machines without an email-configured
# project, it prints a "skip" message and exits 0 cleanly.
#
# Pattern mirrors scripts/install_worker.sh:
#   - bootout-then-bootstrap (idempotent re-install)
#   - inject .env values into plist EnvironmentVariables (avoids macOS TCC
#     hangs on the iCloud-synced ~/Desktop/Valor/.env)
#   - copy ~/Desktop/Valor/projects.json -> config/projects.json so the
#     launchd-loaded bridge reads the local copy under VALOR_LAUNCHD=1
#   - plutil -lint validation before bootstrap
#
# After editing the iCloud-synced vault projects.json, operators must
# re-run this installer to refresh the local config/projects.json copy.

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

PLIST_SRC="$PROJECT_DIR/com.valor.email-bridge.plist"
LABEL="${SERVICE_LABEL_PREFIX}.email-bridge"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"

# -----------------------------------------------------------------------------
# Machine-gate: skip on machines without an email-configured project.
# Mirrors scripts/valor-service.sh::has_bridge_role in shape (Python heredoc
# reading ~/Desktop/Valor/projects.json, exit 0 = qualifies, exit 1 = skip).
# Honors PROJECTS_CONFIG_PATH env override and fails open (returns "qualifies")
# when config is unreadable, matching has_bridge_role's safety posture.
# -----------------------------------------------------------------------------
has_email_role() {
    local config="${PROJECTS_CONFIG_PATH:-$HOME/Desktop/Valor/projects.json}"
    if [ ! -f "$config" ]; then
        return 0
    fi
    if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
        return 0
    fi
    "$PROJECT_DIR/.venv/bin/python" - "$config" <<'PYEOF'
import json, subprocess, sys

try:
    host = subprocess.check_output(
        ["scutil", "--get", "ComputerName"], text=True
    ).strip()
except Exception:
    sys.exit(0)

try:
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
except Exception:
    sys.exit(0)

target = host.lower()
for proj in cfg.get("projects", {}).values():
    if (proj.get("machine") or "").lower() != target:
        continue
    if proj.get("email"):
        sys.exit(0)
sys.exit(1)
PYEOF
}

if ! has_email_role; then
    host=$(scutil --get ComputerName 2>/dev/null || echo unknown)
    echo "Skipping email-bridge install (no email-configured projects assigned to '$host' in projects.json)"
    if [ -f "$PLIST_DST" ]; then
        echo "Removing stale email-bridge plist from non-email machine..."
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "Stale email-bridge plist removed."
    fi
    exit 0
fi

# -----------------------------------------------------------------------------
# Foreground-process pre-check: refuse to install if a non-launchd
# `python -m bridge.email_bridge` is currently running. `bootout` only
# unloads the launchd job; it cannot stop a `valor-service.sh email-start`
# nohup-spawned bridge. Bootstrapping over it would create a double-bridge
# race (duplicate IMAP polling, kill-respawn collisions).
# -----------------------------------------------------------------------------
foreground_pids=""
if pgrep -f "bridge.email_bridge" >/dev/null 2>&1; then
    # PIDs whose parent is not launchd (PPID != 1) are foreground-spawned.
    while IFS= read -r pid; do
        ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || echo "")
        if [ -n "$ppid" ] && [ "$ppid" != "1" ]; then
            foreground_pids="${foreground_pids} ${pid}"
        fi
    done < <(pgrep -f "bridge.email_bridge")
fi

if [ -n "${foreground_pids// /}" ]; then
    cat <<MSG
ERROR: A foreground email bridge is already running (non-launchd PIDs:${foreground_pids}).

Installing the launchd plist now would create a double-bridge race because
bootout only stops the launchd-managed job — it cannot stop a foreground
bridge spawned by 'valor-service.sh email-start' or run manually.

Stop the foreground bridge first:
    ./scripts/valor-service.sh email-stop
or send Ctrl+C to its terminal, then re-run this installer.
MSG
    exit 1
fi

# -----------------------------------------------------------------------------
# Copy iCloud vault files to config/ so the launchd bridge can read them
# without TCC hangs. Mirror install_worker.sh:36-48 verbatim.
# -----------------------------------------------------------------------------
mkdir -p "$PROJECT_DIR/logs"

_copy_config_file() {
    local src="$1" dst="$2" label="$3"
    if [ -f "$src" ]; then
        rm -f "$dst"
        cp "$src" "$dst"
        echo "Copied $label → config/$(basename "$dst")"
    else
        echo "WARNING: $src not found — launchd email bridge will use existing config/$(basename "$dst")"
    fi
}

_copy_config_file "$HOME/Desktop/Valor/projects.json"     "$PROJECT_DIR/config/projects.json"     "projects.json"
_copy_config_file "$HOME/Desktop/Valor/reflections.yaml"  "$PROJECT_DIR/config/reflections.yaml"  "reflections.yaml"

# -----------------------------------------------------------------------------
# Pre-flight checks
# -----------------------------------------------------------------------------
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: Plist template not found at $PLIST_SRC"
    exit 1
fi

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "ERROR: .env file not found. Copy .env.example and configure it."
    exit 1
fi

if [ ! -f "$PROJECT_DIR/.venv/bin/python" ]; then
    echo "ERROR: Virtual environment not found at $PROJECT_DIR/.venv"
    echo "Run: python3 -m venv $PROJECT_DIR/.venv && $PROJECT_DIR/.venv/bin/pip install -e $PROJECT_DIR"
    exit 1
fi

# -----------------------------------------------------------------------------
# Render plist template into ~/Library/LaunchAgents/
# -----------------------------------------------------------------------------
if launchctl list | grep -q "$LABEL" 2>/dev/null; then
    echo "Unloading existing $LABEL..."
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

echo "Installing plist to $PLIST_DST..."
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__HOME_DIR__|$HOME|g; s|__SERVICE_LABEL__|$LABEL|g" "$PLIST_SRC" > "$PLIST_DST"

# -----------------------------------------------------------------------------
# Inject .env values into plist EnvironmentVariables.
# macOS TCC blocks launchd agents from reading ~/Desktop files, so reading
# from the terminal here (which has full access) and baking values into the
# plist avoids a hang on bridge startup.
# -----------------------------------------------------------------------------
echo "Injecting env vars from .env into plist..."
export PROJECT_DIR PLIST_DST
"$PROJECT_DIR/.venv/bin/python" - <<'PYEOF'
import os, sys, plistlib
from pathlib import Path
from dotenv import dotenv_values

project_dir = Path(os.environ.get("PROJECT_DIR", "."))
plist_dst = Path(os.environ.get("PLIST_DST", ""))
env_file = project_dir / ".env"

if not plist_dst:
    print("PLIST_DST not set, skipping env injection", file=sys.stderr)
    sys.exit(0)

try:
    env_vars = dotenv_values(env_file)
except Exception as e:
    print(f"Warning: could not parse .env: {e}", file=sys.stderr)
    sys.exit(0)

with open(plist_dst, "rb") as f:
    plist = plistlib.load(f)

existing = plist.setdefault("EnvironmentVariables", {})
injected = 0
for key, value in env_vars.items():
    if key not in existing and value is not None:
        existing[key] = value
        injected += 1

with open(plist_dst, "wb") as f:
    plistlib.dump(plist, f)

print(f"  Injected {injected} env vars into plist")
PYEOF

# -----------------------------------------------------------------------------
# Validate and bootstrap.
# No watchdog: KeepAlive=true gives launchd-level restart on crash, mirroring
# install_autoexperiment.sh and install_nightly_tests.sh which also rely on
# KeepAlive alone. The com.valor.bridge-watchdog launchd service is specific
# to the Telegram bridge's Telethon session-lock failure mode and does not
# apply to the email bridge.
# -----------------------------------------------------------------------------
if ! plutil -lint "$PLIST_DST" > /dev/null; then
    echo "ERROR: Generated plist is invalid"
    exit 1
fi

echo "Loading $LABEL..."
launchctl_bootstrap_fail_soft "gui/$(id -u)" "$PLIST_DST" "$LABEL" verify-pid || exit 1

echo ""
echo "Email bridge service installed successfully."
echo "  Logs: $PROJECT_DIR/logs/email_bridge.log"
echo "  Errors: $PROJECT_DIR/logs/email_bridge.error.log"
echo ""
echo "To check status: launchctl list | grep email-bridge"
echo "To stop: launchctl bootout gui/$(id -u)/$LABEL"
echo "To run manually: ./scripts/valor-service.sh email-start"
