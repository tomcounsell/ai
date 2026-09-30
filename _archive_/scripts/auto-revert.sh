#!/bin/bash
# Auto-revert: revert HEAD commit and restart bridge.
# Called by bridge_watchdog.py when crash pattern detected.
#
# This is a RECOVERY tool, not a development tool. It should only
# be triggered automatically when the bridge detects repeated crashes
# correlated with a recent commit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_PREFIX="[auto-revert]"

cd "$PROJECT_DIR"

echo "$LOG_PREFIX Starting auto-revert..."

# Check if there's actually a commit to revert
if ! git rev-parse HEAD~1 >/dev/null 2>&1; then
    echo "$LOG_PREFIX ERROR: No previous commit to revert to"
    exit 1
fi

# Get current and previous commit info
CURRENT_SHA=$(git rev-parse --short HEAD)
PREVIOUS_SHA=$(git rev-parse --short HEAD~1)
CURRENT_MSG=$(git log -1 --format=%s HEAD)

echo "$LOG_PREFIX Current HEAD: $CURRENT_SHA - $CURRENT_MSG"
echo "$LOG_PREFIX Reverting to: $PREVIOUS_SHA"

# Create revert commit
if ! git revert HEAD --no-edit; then
    echo "$LOG_PREFIX ERROR: Git revert failed"
    exit 1
fi

echo "$LOG_PREFIX Revert commit created"

# Push the revert (so other machines don't pull the bad commit)
if git push origin HEAD 2>/dev/null; then
    echo "$LOG_PREFIX Pushed revert to remote"
else
    echo "$LOG_PREFIX WARNING: Could not push to remote (will push later)"
fi

# Restart bridge
echo "$LOG_PREFIX Restarting bridge..."
"$SCRIPT_DIR/valor-service.sh" restart

echo "$LOG_PREFIX Auto-revert complete"
echo "$LOG_PREFIX Reverted: $CURRENT_SHA -> $PREVIOUS_SHA"
