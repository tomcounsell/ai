#!/bin/bash

# Server start script with hot reload
# Checks if server is already running, starts it if not

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
        echo "Hot reload enabled - code changes auto-refresh"
        
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

# Main logic
if check_server; then
    exit 0
else
    start_server
fi
