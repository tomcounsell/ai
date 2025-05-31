#!/bin/bash

# Unified server start script with Telegram authentication
# This script ensures both the FastAPI server and Telegram client start together

PORT=9000
PID_FILE="/tmp/fastapi_server.pid"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Function to check if server is running
check_server() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            echo "Server is already running on port $PORT (PID: $PID)"
            echo "Visit: http://localhost:$PORT/"
            echo "API docs: http://localhost:$PORT/docs"
            return 0
        else
            echo "Stale PID file found, removing..."
            rm -f "$PID_FILE"
        fi
    fi
    return 1
}

# Function to start server
start_server() {
    echo "Starting FastAPI development server with hot reload..."
    cd "$PROJECT_ROOT" || exit 1
    
    # Check if main.py exists
    if [ ! -f "$PROJECT_ROOT/main.py" ]; then
        echo "Error: main.py not found in project root ($PROJECT_ROOT)"
        echo "Please create main.py first or run from the correct directory"
        exit 1
    fi

    # Check if uvicorn is available
    if ! command -v uvicorn &> /dev/null; then
        echo "uvicorn not found. Installing..."
        pip install uvicorn[standard] fastapi
    fi

    # Create logs directory if it doesn't exist
    mkdir -p "$PROJECT_ROOT/logs"
    LOG_FILE="$PROJECT_ROOT/logs/server.log"

    # Start server in background and redirect output to log file
    echo "Launching server in background (logs: logs/server.log)..."
    nohup uvicorn main:app --host 0.0.0.0 --port $PORT --reload > "$LOG_FILE" 2>&1 &
    SERVER_PID=$!
    echo $SERVER_PID > "$PID_FILE"

    # Wait a moment for server to start
    sleep 3

    # Check if server started successfully
    if ps -p $SERVER_PID > /dev/null 2>&1; then
        echo "✅ Server started successfully on port $PORT (PID: $SERVER_PID)"
        echo ""
        echo "🌐 URLs:"
        echo "  Main:     http://localhost:$PORT/"
        echo "  API docs: http://localhost:$PORT/docs"
        echo "  Redoc:    http://localhost:$PORT/redoc"
        echo ""
        echo "📁 Logs:    tail -f logs/server.log"
        echo "🛑 Stop:    scripts/stop.sh"
        echo ""
        echo "🤖 Services running:"
        echo "  ✅ FastAPI server (with hot reload)"
        echo "  ✅ Telegram client (authenticated)"
        echo ""
        echo "Ready to receive Telegram messages!"
        
        # Show last few lines of log to confirm startup
        echo ""
        echo "📋 Recent startup logs:"
        tail -n 5 "$LOG_FILE" 2>/dev/null || echo "  (logs not available yet)"
    else
        echo "❌ Failed to start server"
        echo "📋 Check logs for details:"
        cat "$LOG_FILE" 2>/dev/null || echo "  (no log file found)"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# Function to check Telegram authentication
check_telegram_auth() {
    echo "🔍 Checking Telegram authentication..."
    cd "$PROJECT_ROOT" || exit 1
    
    # Check if session file exists
    if [ ! -f "ai_project_bot.session" ]; then
        echo "⚠️  No Telegram session found"
        return 1
    fi
    
    # Test the session validity
    python -c "
import asyncio
import sys
from integrations.telegram.client import TelegramClient

async def test_auth():
    try:
        client = TelegramClient()
        success = await client.initialize()
        if success and client.is_connected:
            me = await client.client.get_me()
            print(f'✅ Telegram authenticated as: {me.first_name} (@{me.username})')
            await client.stop()
            return True
        else:
            print('❌ Telegram session invalid')
            await client.stop() if client else None
            return False
    except Exception as e:
        print(f'❌ Telegram auth check failed: {e}')
        return False

result = asyncio.run(test_auth())
sys.exit(0 if result else 1)
" 2>/dev/null
    
    return $?
}

# Function to run Telegram authentication
authenticate_telegram() {
    echo ""
    echo "🔐 Telegram authentication required"
    echo "This will prompt for your phone number and verification code"
    echo ""
    
    if [ -t 0 ]; then
        # Interactive terminal available
        "$SCRIPT_DIR/telegram_login.sh"
        return $?
    else
        # Non-interactive environment
        echo "❌ Telegram authentication requires interactive terminal"
        echo ""
        echo "Please run this command in an interactive terminal:"
        echo "  scripts/telegram_login.sh"
        echo ""
        echo "Then start the server again with:"
        echo "  scripts/start.sh"
        return 1
    fi
}

# Main logic
if check_server; then
    exit 0
fi

# Check Telegram authentication before starting server
if ! check_telegram_auth; then
    if ! authenticate_telegram; then
        echo ""
        echo "❌ Cannot start server without Telegram authentication"
        echo ""
        echo "To fix this:"
        echo "1. Run: scripts/telegram_login.sh"
        echo "2. Enter your phone number and verification code"
        echo "3. Run: scripts/start.sh again"
        exit 1
    fi
fi

echo "✅ Telegram authentication verified"
echo ""

start_server
