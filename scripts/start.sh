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

# Function to check if Huey consumer is running
check_huey() {
    if pgrep -f "huey_consumer.py" > /dev/null; then
        HUEY_PID=$(pgrep -f "huey_consumer.py")
        echo "✅ Huey consumer is already running (PID: $HUEY_PID)"
        return 0
    fi
    return 1
}

# Function to start Huey consumer
start_huey() {
    echo "🚀 Starting Huey task queue consumer..."
    
    # Set production environment variables
    export HUEY_DB_PATH="data/huey.db"
    export HUEY_IMMEDIATE="false"
    
    # Ensure data directory exists
    mkdir -p "$PROJECT_ROOT/data" "$PROJECT_ROOT/logs"
    
    # Start Huey consumer in background (logs to tasks.log via Python logging)
    python "$PROJECT_ROOT/huey_consumer.py" tasks.huey_config.huey \
        -w 1 \
        -k thread \
        -v &
    
    HUEY_PID=$!
    echo $HUEY_PID > "$PROJECT_ROOT/huey.pid"
    
    # Wait a moment for Huey to start
    sleep 2
    
    # Check if Huey started successfully
    if ps -p $HUEY_PID > /dev/null 2>&1; then
        echo "✅ Huey consumer started successfully (PID: $HUEY_PID)"
        return 0
    else
        echo "❌ Failed to start Huey consumer"
        echo "📋 Check logs: tail -f logs/tasks.log"
        rm -f "$PROJECT_ROOT/huey.pid"
        return 1
    fi
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

    # Start server in background (logs to system.log via Python logging)
    echo "Launching server in background (logs: logs/system.log)..."
    nohup uvicorn main:app --host 0.0.0.0 --port $PORT --reload > /dev/null 2>&1 &
    SERVER_PID=$!
    echo $SERVER_PID > "$PID_FILE"

    # Wait a moment for server to start
    sleep 5

    # Check if server started successfully
    if ps -p $SERVER_PID > /dev/null 2>&1; then
        echo "✅ Server started successfully on port $PORT (PID: $SERVER_PID)"
        echo ""
        echo "🌐 URLs:"
        echo "  Main:     http://localhost:$PORT/"
        echo "  API docs: http://localhost:$PORT/docs"
        echo "  Redoc:    http://localhost:$PORT/redoc"
        echo ""
        echo "📁 Logs:"
        echo "  System:   tail -f logs/system.log"
        echo "  Tasks:    tail -f logs/tasks.log"
        echo "🛑 Stop:    scripts/stop.sh"
        echo ""
        echo "🤖 Services running:"
        echo "  ✅ FastAPI server (with hot reload)"
        echo "  ✅ Telegram client (authenticated)"
        echo "  ✅ Huey task queue (background processing)"
        echo ""
        echo "Ready to receive Telegram messages and process background tasks!"
        echo ""
        echo "🚀 Server is running in background. Check logs with:"
        echo "  tail -f logs/system.log"
    else
        echo "❌ Failed to start server"
        echo "📋 Check logs for details:"
        tail -20 "logs/system.log" 2>/dev/null || echo "  (no log file found)"
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
    # Server is running, but check if Huey is also running
    if ! check_huey; then
        echo ""
        echo "🚀 Server is running, but Huey consumer is not. Starting Huey..."
        if ! start_huey; then
            echo "❌ Failed to start Huey consumer"
            exit 1
        fi
    fi
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

# Function to prevent database locks by cleaning up stale processes
prevent_database_locks() {
    echo "🔧 Checking for potential database lock issues..."
    
    # Clean up any processes holding the Telegram session file
    SESSION_FILE="$PROJECT_ROOT/ai_project_bot.session"
    if [ -f "$SESSION_FILE" ]; then
        LOCKING_PIDS=$(lsof "$SESSION_FILE" 2>/dev/null | awk 'NR>1 {print $2}' | sort -u)
        if [ -n "$LOCKING_PIDS" ]; then
            echo "⚠️  Found processes holding Telegram session file, cleaning up..."
            echo "$LOCKING_PIDS" | xargs kill -9 2>/dev/null
            sleep 1
            echo "✅ Session file cleanup complete"
        fi
    fi
    
    # Check for orphaned Python processes that might interfere
    ORPHANED_PYTHON=$(pgrep -f "python.*main.py" 2>/dev/null)
    if [ -n "$ORPHANED_PYTHON" ]; then
        echo "⚠️  Found orphaned Python processes, cleaning up..."
        echo "$ORPHANED_PYTHON" | xargs kill -9 2>/dev/null
        sleep 1
        echo "✅ Orphaned process cleanup complete"
    fi
    
    echo "✅ Database lock prevention check complete"
}

echo "✅ Telegram authentication verified"
echo ""

# Prevent database locks before starting
prevent_database_locks
echo ""

# Start Huey consumer first
if ! check_huey; then
    if ! start_huey; then
        echo "❌ Failed to start Huey consumer"
        exit 1
    fi
    echo ""
fi

start_server
