#!/bin/bash
# Install the autoexperiment launchd schedule for nightly 2 AM runs.
#
# Usage:
#   ./scripts/install_autoexperiment.sh [--target observer|summarizer|stage_detector]
#
# This installs a launchd plist that runs autoexperiment nightly at 2 AM
# with a $2.00 budget ceiling and 100 iteration cap.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="com.valor.autoexperiment"
PLIST_SRC="$PROJECT_DIR/$PLIST_NAME.plist"
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
sed "s|__TARGET__|$TARGET|g; s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PLIST_SRC" > "$PLIST_DEST"

# Load
launchctl load "$PLIST_DEST"

echo "Installed successfully."
echo ""
echo "Commands:"
echo "  Check status:  launchctl list | grep autoexperiment"
echo "  View logs:     tail -f $PROJECT_DIR/logs/autoexperiment.log"
echo "  Stop:          touch $PROJECT_DIR/data/experiments/STOP"
echo "  Uninstall:     launchctl unload $PLIST_DEST && rm $PLIST_DEST"
