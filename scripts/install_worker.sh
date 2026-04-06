#!/bin/bash
# Install the standalone worker as a launchd service.
# Usage: ./scripts/install_worker.sh
#
# The worker processes AgentSession records from Redis without Telegram.
# Dev workstations run this instead of the bridge.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_SRC="$PROJECT_DIR/com.valor.worker.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.valor.worker.plist"
LABEL="com.valor.worker"

# Ensure logs directory exists
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/logs/worker"

# Check source plist exists
if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: Plist not found at $PLIST_SRC"
    exit 1
fi

# Check .env exists
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "ERROR: .env file not found. Copy .env.example and configure it."
    exit 1
fi

# Check venv exists
if [ ! -f "$PROJECT_DIR/.venv/bin/python" ]; then
    echo "ERROR: Virtual environment not found at $PROJECT_DIR/.venv"
    echo "Run: python3 -m venv $PROJECT_DIR/.venv && $PROJECT_DIR/.venv/bin/pip install -e $PROJECT_DIR"
    exit 1
fi

# Verify worker can start (dry-run)
echo "Verifying worker configuration..."
if ! "$PROJECT_DIR/.venv/bin/python" -m worker --dry-run 2>&1; then
    echo "ERROR: Worker dry-run failed. Fix configuration before installing."
    exit 1
fi

# Unload current version if present
if launchctl list | grep -q "$LABEL"; then
    echo "Unloading existing $LABEL..."
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

# Copy plist to LaunchAgents with path substitution
echo "Installing plist to $PLIST_DST..."
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__HOME_DIR__|$HOME|g" "$PLIST_SRC" > "$PLIST_DST"

# Validate plist
if ! plutil -lint "$PLIST_DST" > /dev/null; then
    echo "ERROR: Generated plist is invalid"
    exit 1
fi

# Load new version
echo "Loading $LABEL..."
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"

echo ""
echo "Worker service installed successfully."
echo "  Logs: $PROJECT_DIR/logs/worker.log"
echo "  Errors: $PROJECT_DIR/logs/worker_error.log"
echo "  Output: $PROJECT_DIR/logs/worker/ (per-session)"
echo ""
echo "To check status: launchctl list | grep worker"
echo "To stop: launchctl bootout gui/$(id -u)/$LABEL"
echo "To run manually: python -m worker"
