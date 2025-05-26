#!/bin/bash
# Demo script for the Telegram Chat Agent
# Runs comprehensive tests in the background and logs output

cd "$(dirname "$0")/.."

echo "🚀 Starting Telegram Chat Agent Demo..."
echo "Running comprehensive test battery in background..."

# Create logs directory if it doesn't exist
mkdir -p logs

# Run the demo and capture output
nohup python tests/test_agent_demo.py > logs/agent_demo.log 2>&1 &
demo_pid=$!

echo "Demo started with PID: $demo_pid"
echo "Monitor progress with: tail -f logs/agent_demo.log"
echo "Or wait for completion with: wait $demo_pid"

# Save PID for easy cleanup
echo $demo_pid > logs/agent_demo.pid

echo "✅ Demo running in background"