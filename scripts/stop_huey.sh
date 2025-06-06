#!/bin/bash
# Stop Huey consumer

echo "🛑 Stopping Huey consumer..."

# Check if PID file exists
if [ -f "huey.pid" ]; then
    PID=$(cat huey.pid)
    if ps -p $PID > /dev/null; then
        kill $PID
        echo "✅ Stopped Huey consumer (PID: $PID)"
    else
        echo "⚠️  Huey consumer not running (stale PID file)"
    fi
    rm huey.pid
else
    echo "⚠️  No PID file found"
fi

# Clean up any orphaned processes
pkill -f "huey_consumer.py" 2>/dev/null && echo "✅ Cleaned up orphaned processes"

echo "✅ Huey consumer stopped"