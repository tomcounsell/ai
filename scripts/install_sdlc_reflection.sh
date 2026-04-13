#!/bin/bash
# Install the SDLC reflection launchd service (runs every 3 days).
# Usage: ./scripts/install_sdlc_reflection.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

set -a
# shellcheck disable=SC1091
[ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
set +a
: "${SERVICE_LABEL_PREFIX:=com.valor}"

PLIST_SRC="$PROJECT_DIR/com.valor.sdlc-reflection.plist"
LABEL="${SERVICE_LABEL_PREFIX}.sdlc-reflection"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"

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
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"

echo ""
echo "SDLC reflection service installed successfully."
echo "Label:    $LABEL"
echo "Schedule: every 3 days (259200s interval)"
echo "Log:      $PROJECT_DIR/logs/reflections.log"
echo ""
echo "To run manually: python scripts/sdlc_reflection.py --dry-run"
echo "To uninstall:    launchctl bootout gui/$(id -u)/$LABEL && rm $PLIST_DST"
