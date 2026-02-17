#!/bin/bash
# Install the daydream launchd service for daily 6 AM Pacific scheduling.
# Usage: ./scripts/install_daydream.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_SRC="$PROJECT_DIR/com.valor.daydream.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.valor.daydream.plist"
LABEL="com.valor.daydream"

# Ensure logs directory exists
mkdir -p "$PROJECT_DIR/logs"

# Check source plist exists
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: Plist not found at $PLIST_SRC"
    exit 1
fi

# Unload old version if present
if launchctl list | grep -q "$LABEL"; then
    echo "Unloading existing $LABEL..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# Copy plist to LaunchAgents
echo "Installing plist to $PLIST_DST..."
cp "$PLIST_SRC" "$PLIST_DST"

# Load new version
echo "Loading $LABEL..."
launchctl load "$PLIST_DST"

echo ""
echo "Daydream service installed successfully."
echo "  Schedule: Daily at 6:00 AM Pacific"
echo "  Logs: $PROJECT_DIR/logs/daydream.log"
echo "  Errors: $PROJECT_DIR/logs/daydream_error.log"
echo ""
echo "To check status: launchctl list | grep daydream"
echo "To run manually: python $PROJECT_DIR/scripts/daydream.py"
