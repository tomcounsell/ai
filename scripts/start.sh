#!/bin/bash

# Server start script with hot reload
# Checks if server is already running, starts it if not

PORT=8000
PID_FILE="/tmp/fastapi_server.pid"

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
    cd "$(dirname "$0")/.." || exit 1

    # Check if main.py exists
    if [ ! -f "main.py" ]; then
        echo "Error: main.py not found in project root"
        echo "Please create main.py first or run from the correct directory"
        exit 1
    fi

    # Check if uvicorn is available
    if ! command -v uvicorn &> /dev/null; then
        echo "uvicorn not found. Installing..."
        pip install uvicorn[standard] fastapi
    fi

    # Start server in background and save PID
    uvicorn main:app --host 0.0.0.0 --port $PORT --reload &
    SERVER_PID=$!
    echo $SERVER_PID > "$PID_FILE"

    # Wait a moment for server to start
    sleep 3

    # Check if server started successfully
    if ps -p $SERVER_PID > /dev/null 2>&1; then
        echo "Server started successfully on port $PORT (PID: $SERVER_PID)"
        echo "Visit: http://localhost:$PORT/"
        echo "API docs: http://localhost:$PORT/docs"
        echo "Alternative docs: http://localhost:$PORT/redoc"
        echo "Hot reload is enabled - code changes will be reflected automatically"
        echo "To stop the server, run: scripts/stop.sh"
    else
        echo "Failed to start server"
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
