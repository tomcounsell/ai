#!/bin/bash
# Install the reflections launchd service for daily 6 AM Pacific scheduling.
# Usage: ./scripts/install_reflections.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_SRC="$PROJECT_DIR/com.valor.reflections.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.valor.reflections.plist"
LABEL="com.valor.reflections"
OLD_LABEL="com.valor.daydream"
OLD_PLIST_DST="$HOME/Library/LaunchAgents/com.valor.daydream.plist"

# Ensure logs directory exists
mkdir -p "$PROJECT_DIR/logs"

# Check source plist exists
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: Plist not found at $PLIST_SRC"
    exit 1
fi

# Unload old daydream service if present (migration from daydream -> reflections)
if launchctl list | grep -q "$OLD_LABEL"; then
    echo "Unloading old $OLD_LABEL service..."
    launchctl unload "$OLD_PLIST_DST" 2>/dev/null || true
    rm -f "$OLD_PLIST_DST"
fi

# Unload current version if present
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
echo "Reflections service installed successfully."
echo "  Schedule: Daily at 6:00 AM Pacific"
echo "  Logs: $PROJECT_DIR/logs/reflections.log"
echo "  Errors: $PROJECT_DIR/logs/reflections_error.log"
echo ""
echo "To check status: launchctl list | grep reflections"
echo "To run manually: python $PROJECT_DIR/scripts/reflections.py"
