#!/bin/bash

# Unified shutdown script
# Stops both the FastAPI server and Huey consumer

PID_FILE="/tmp/fastapi_server.pid"
HUEY_PID_FILE="huey.pid"

# Function to stop Huey consumer
stop_huey() {
    echo "🛑 Stopping Huey consumer..."
    
    if [ -f "$HUEY_PID_FILE" ]; then
        PID=$(cat "$HUEY_PID_FILE")
        if ps -p $PID > /dev/null 2>&1; then
            echo "  Stopping Huey consumer (PID: $PID)..."
            kill $PID
            
            # Wait for process to stop
            sleep 2
            
            # Check if process is still running
            if ps -p $PID > /dev/null 2>&1; then
                echo "  Process still running, forcing termination..."
                kill -9 $PID
                sleep 1
            fi
            
            echo "  ✅ Huey consumer stopped successfully"
        else
            echo "  ⚠️  Huey consumer not running (stale PID file)"
        fi
        rm -f "$HUEY_PID_FILE"
    else
        echo "  ⚠️  No Huey PID file found"
    fi
    
    # Clean up any orphaned Huey processes
    HUEY_PIDS=$(pgrep -f "huey_consumer.py" 2>/dev/null)
    if [ -n "$HUEY_PIDS" ]; then
        echo "  🧹 Cleaning up orphaned Huey processes..."
        echo "$HUEY_PIDS" | xargs kill -9 2>/dev/null
        echo "  ✅ Orphaned Huey processes cleaned up"
    fi
}

# Function to stop server
stop_server() {
    echo "🛑 Stopping FastAPI server..."
    
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")

        if ps -p $PID > /dev/null 2>&1; then
            echo "  Stopping FastAPI server (PID: $PID)..."
            kill $PID

            # Wait for process to stop
            sleep 2

            # Check if process is still running
            if ps -p $PID > /dev/null 2>&1; then
                echo "  Process still running, forcing termination..."
                kill -9 $PID
                sleep 1
            fi

            # Remove PID file
            rm -f "$PID_FILE"
            echo "  ✅ FastAPI server stopped successfully"
        else
            echo "  ⚠️  Server is not running (stale PID file found)"
            rm -f "$PID_FILE"
        fi
    else
        echo "  ⚠️  Server is not running (no PID file found)"
    fi
}

# Check for any uvicorn processes and kill them
cleanup_orphaned_processes() {
    echo "🧹 Cleaning up orphaned processes..."
    
    UVICORN_PIDS=$(pgrep -f "uvicorn.*main:app" 2>/dev/null)
    if [ -n "$UVICORN_PIDS" ]; then
        echo "  Found orphaned uvicorn processes, cleaning up..."
        echo "$UVICORN_PIDS" | xargs kill 2>/dev/null
        sleep 1

        # Force kill if still running
        REMAINING_PIDS=$(pgrep -f "uvicorn.*main:app" 2>/dev/null)
        if [ -n "$REMAINING_PIDS" ]; then
            echo "  Force killing remaining uvicorn processes..."
            echo "$REMAINING_PIDS" | xargs kill -9 2>/dev/null
        fi
        echo "  ✅ Uvicorn cleanup complete"
    fi
}

# Clean up any processes holding Telegram session file
cleanup_telegram_session() {
    echo "🧹 Cleaning up Telegram session..."
    
    SESSION_FILE="ai_project_bot.session"
    if [ -f "$SESSION_FILE" ]; then
        LOCKING_PIDS=$(lsof "$SESSION_FILE" 2>/dev/null | awk 'NR>1 {print $2}' | sort -u)
        if [ -n "$LOCKING_PIDS" ]; then
            echo "  Found processes holding Telegram session, cleaning up..."
            echo "$LOCKING_PIDS" | xargs kill -9 2>/dev/null
            sleep 1
            echo "  ✅ Telegram session cleanup complete"
        fi
    fi
}

# Main logic
echo "🛑 UNIFIED SYSTEM SHUTDOWN"
echo "=============================="

# Stop both services
stop_huey
stop_server

# Clean up orphaned processes
cleanup_orphaned_processes
cleanup_telegram_session

echo ""
echo "✅ ALL SERVICES STOPPED"
echo "  • FastAPI server"
echo "  • Huey task queue consumer"
echo "  • Orphaned processes cleaned up"
echo ""
echo "🚀 To restart: scripts/start.sh"
