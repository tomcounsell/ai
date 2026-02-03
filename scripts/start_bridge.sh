#!/bin/bash
# Start the Telegram bridge

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/.venv"

cd "$PROJECT_DIR"

# Ensure data directory exists
mkdir -p "$PROJECT_DIR/data"

# Atomic process lock (prevents concurrent starts)
LOCK_DIR="$PROJECT_DIR/data/bridge-start.lock"
cleanup_lock() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "ERROR: Another bridge start/stop operation is in progress."
    echo "If this persists, remove: $LOCK_DIR"
    exit 1
fi
trap cleanup_lock EXIT

# Warn about pending critical dependency upgrades
if [ -f "$PROJECT_DIR/data/upgrade-pending" ]; then
    echo "WARNING: Critical dependency upgrade pending. Run /update to apply."
    cat "$PROJECT_DIR/data/upgrade-pending"
fi

# Kill any existing bridge processes
EXISTING_PID=$(pgrep -f "python.*telegram_bridge.py" 2>/dev/null || true)
if [ -n "$EXISTING_PID" ]; then
    echo "Stopping existing bridge (PID: $EXISTING_PID)..."
    kill $EXISTING_PID 2>/dev/null || true
    sleep 3

    # Verify process actually stopped
    if pgrep -f "python.*telegram_bridge.py" >/dev/null 2>&1; then
        echo "Force killing bridge..."
        pkill -9 -f "python.*telegram_bridge.py" 2>/dev/null || true
        sleep 1
    fi
fi

# Ensure virtual environment exists
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV"
fi

# Ensure dependencies are installed (use explicit venv paths, no user-site)
if ! "$VENV/bin/python" -c "import telethon; import httpx; import dotenv" 2>/dev/null; then
    echo "Installing dependencies..."
    "$VENV/bin/pip" install -e . 2>&1
fi

# Check for required config files
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found."
    echo "  cp .env.example .env"
    echo "  # Then edit .env with your credentials"
    exit 1
fi

if [ ! -f "config/projects.json" ]; then
    echo "ERROR: config/projects.json not found."
    echo "  cp config/projects.json.example config/projects.json"
    echo "  # Then edit with your project settings"
    exit 1
fi

# Check for Telegram session
if ! ls data/*.session 2>/dev/null | grep -q .; then
    echo "WARNING: No Telegram session found. Run first:"
    echo "  $VENV/bin/python scripts/telegram_login.py"
fi

# Start the bridge
echo "Starting Telegram-Clawdbot bridge..."
exec "$VENV/bin/python" bridge/telegram_bridge.py
