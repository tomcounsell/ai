#!/bin/bash
# Start Huey consumer with production settings

# BEST PRACTICE: Use environment variables for configuration
export HUEY_DB_PATH="data/huey.db"
export HUEY_IMMEDIATE="false"

# Ensure data directory exists
mkdir -p data logs

echo "🚀 Starting Huey consumer..."

# Check if consumer is already running
if pgrep -f "huey_consumer.py" > /dev/null; then
    echo "⚠️  Huey consumer is already running"
    exit 1
fi

# Start consumer with 4 threads
# IMPLEMENTATION NOTE: Adjust worker count based on load
python huey_consumer.py tasks.huey_config.huey \
    -w 4 \
    -k thread \
    -l logs/huey.log \
    -v &

# Save PID
echo $! > huey.pid

echo "✅ Huey consumer started (PID: $(cat huey.pid))"
echo "📋 Logs: tail -f logs/huey.log"
echo "🛑 Stop: scripts/stop_huey.sh"