#!/bin/bash
# Start the Telegram-Clawdbot bridge

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Check if dependencies are installed
if ! python -c "import telethon" 2>/dev/null; then
    echo "Installing dependencies..."
    pip install telethon python-dotenv
fi

# Start the bridge
echo "Starting Telegram-Clawdbot bridge..."
exec python bridge/telegram_bridge.py
