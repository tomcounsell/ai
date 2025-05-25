#!/bin/bash

# Server shutdown script
# Stops the FastAPI development server

PID_FILE="/tmp/fastapi_server.pid"

# Function to stop server
stop_server() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        
        if ps -p $PID > /dev/null 2>&1; then
            echo "Stopping FastAPI server (PID: $PID)..."
            kill $PID
            
            # Wait for process to stop
            sleep 2
            
            # Check if process is still running
            if ps -p $PID > /dev/null 2>&1; then
                echo "Process still running, forcing termination..."
                kill -9 $PID
                sleep 1
            fi
            
            # Remove PID file
            rm -f "$PID_FILE"
            echo "Server stopped successfully"
        else
            echo "Server is not running (stale PID file found)"
            rm -f "$PID_FILE"
        fi
    else
        echo "Server is not running (no PID file found)"
    fi
}

# Check for any uvicorn processes and kill them
cleanup_orphaned_processes() {
    UVICORN_PIDS=$(pgrep -f "uvicorn.*main:app")
    
    if [ -n "$UVICORN_PIDS" ]; then
        echo "Found orphaned uvicorn processes, cleaning up..."
        echo "$UVICORN_PIDS" | xargs kill 2>/dev/null
        sleep 1
        
        # Force kill if still running
        REMAINING_PIDS=$(pgrep -f "uvicorn.*main:app")
        if [ -n "$REMAINING_PIDS" ]; then
            echo "Force killing remaining processes..."
            echo "$REMAINING_PIDS" | xargs kill -9 2>/dev/null
        fi
        echo "Cleanup complete"
    fi
}

# Main logic
stop_server
cleanup_orphaned_processes

echo "All FastAPI server processes stopped"