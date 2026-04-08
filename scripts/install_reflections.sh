#!/bin/bash
# Install the reflections launchd service for daily 6 AM Pacific scheduling.
# Usage: ./scripts/install_reflections.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

set -a
# shellcheck disable=SC1091
[ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
set +a
: "${SERVICE_LABEL_PREFIX:=com.valor}"

PLIST_SRC="$PROJECT_DIR/com.valor.reflections.plist"
LABEL="${SERVICE_LABEL_PREFIX}.reflections"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
# Legacy daydream label is hard-pinned to com.valor — it only ever existed
# under that prefix, so the cleanup runs regardless of the fork's prefix.
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
    launchctl bootout "gui/$(id -u)/$OLD_LABEL" 2>/dev/null || true
    rm -f "$OLD_PLIST_DST"
fi

# Unload current version if present
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

# Render newsyslog config from template (Task 4) and print install instruction.
NEWSYSLOG_TEMPLATE="$PROJECT_DIR/config/newsyslog.conf.template"
NEWSYSLOG_RENDERED="$PROJECT_DIR/config/newsyslog.rendered.conf"
if [ -f "$NEWSYSLOG_TEMPLATE" ]; then
    sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$NEWSYSLOG_TEMPLATE" > "$NEWSYSLOG_RENDERED"
    echo ""
    echo "Rendered newsyslog config to $NEWSYSLOG_RENDERED"
    echo "To install (requires root — macOS newsyslog only reads /etc/newsyslog.d/):"
    echo "  sudo cp $NEWSYSLOG_RENDERED /etc/newsyslog.d/valor.conf"
fi

echo ""
echo "Reflections service installed successfully."
echo "  Schedule: Daily at 6:00 AM Pacific"
echo "  Logs: $PROJECT_DIR/logs/reflections.log"
echo "  Errors: $PROJECT_DIR/logs/reflections_error.log"
echo ""
echo "To check status: launchctl list | grep reflections"
echo "To run manually: python $PROJECT_DIR/scripts/reflections.py"
