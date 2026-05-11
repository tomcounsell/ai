#!/bin/bash
# Install the autoexperiment launchd schedule for nightly 2 AM runs.
#
# Usage:
#   ./scripts/install_autoexperiment.sh [--target observer|summarizer]
#
# This installs a launchd plist that runs autoexperiment nightly at 2 AM
# with a $2.00 budget ceiling and 100 iteration cap.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

set -a
# shellcheck disable=SC1091
[ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
set +a
: "${SERVICE_LABEL_PREFIX:=com.valor}"

PLIST_NAME="${SERVICE_LABEL_PREFIX}.autoexperiment"
PLIST_SRC="$PROJECT_DIR/com.valor.autoexperiment.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

# Default target
TARGET="${1:-observer}"
if [[ "$TARGET" == "--target" ]]; then
    TARGET="${2:-observer}"
fi

echo "Installing autoexperiment schedule..."
echo "  Target: $TARGET"
echo "  Schedule: Nightly at 2:00 AM"
echo "  Budget: \$2.00 per run"
echo "  Project: $PROJECT_DIR"

# Check plist exists
if [[ ! -f "$PLIST_SRC" ]]; then
    echo "ERROR: Plist not found at $PLIST_SRC"
    exit 1
fi

# Unload existing if present
if launchctl list | grep -q "$PLIST_NAME" 2>/dev/null; then
    echo "Unloading existing schedule..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# Copy plist (substitute target if needed)
sed "s|__TARGET__|$TARGET|g; s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__SERVICE_LABEL__|$PLIST_NAME|g" "$PLIST_SRC" > "$PLIST_DEST"

# Inject env vars into the plist. The helper auto-selects lean vs full
# injection by the vault's TCC status — secrets only land in the plist
# (and chmod 0600 applied) when the vault is on a TCC-restricted path.
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="python3"
VAULT_DIR="${VALOR_VAULT_DIR:-$HOME/.valor}"
[ -d "$VAULT_DIR" ] || VAULT_DIR="$HOME/Desktop/Valor"  # legacy compat
"$PYTHON_BIN" "$SCRIPT_DIR/install/inject_plist_env.py" \
    --plist "$PLIST_DEST" \
    --env-file "$PROJECT_DIR/.env" \
    --vault-dir "$VAULT_DIR"

# Load
launchctl load "$PLIST_DEST"

echo "Installed successfully."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep autoexperiment"
echo "  View logs:     tail -f $PROJECT_DIR/logs/autoexperiment.log"
echo "  Stop:          touch $PROJECT_DIR/data/experiments/STOP"
echo "  Uninstall:     launchctl unload $PLIST_DEST && rm $PLIST_DEST"
