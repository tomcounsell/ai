#!/bin/bash
# Install the issue poller launchd service for 5-minute polling.
# Usage: ./scripts/install_issue_poller.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_SRC="$PROJECT_DIR/com.valor.issue-poller.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.valor.issue-poller.plist"
LABEL="com.valor.issue-poller"

# Ensure logs directory exists
mkdir -p "$PROJECT_DIR/logs"

# Check source plist exists
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: Plist not found at $PLIST_SRC"
    exit 1
fi

# Unload current version if present
if launchctl list | grep -q "$LABEL"; then
    echo "Unloading existing $LABEL..."
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

# Copy plist to LaunchAgents
echo "Installing plist to $PLIST_DST..."
cp "$PLIST_SRC" "$PLIST_DST"

# Load new version
echo "Loading $LABEL..."
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"

echo ""
echo "Issue poller service installed successfully."
echo "  Schedule: Every 5 minutes"
echo "  Logs: $PROJECT_DIR/logs/issue_poller.log"
echo "  Errors: $PROJECT_DIR/logs/issue_poller_error.log"
echo ""
echo "To check status: launchctl list | grep issue-poller"
echo "To run manually: python $PROJECT_DIR/scripts/issue_poller.py"
echo "To unload: launchctl bootout gui/$(id -u)/$LABEL"
