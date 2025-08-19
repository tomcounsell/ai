#!/bin/bash

# AI Rebuild System - Shutdown Script
# This script cleanly stops the AI Rebuild system

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo -e "${YELLOW}🛑 AI Rebuild System Shutdown${NC}"
echo "================================"

# Change to project root
cd "$PROJECT_ROOT"

# Check for virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Run the Python shutdown script
if [ -f "scripts/shutdown.py" ]; then
    echo -e "${GREEN}Running clean shutdown...${NC}"
    python3 scripts/shutdown.py
else
    echo -e "${YELLOW}⚠ Python shutdown script not found${NC}"
    echo "Attempting to stop processes manually..."
    
    # Try to stop known processes
    echo "Stopping Python processes..."
    
    # Find and kill demo_server.py
    if pgrep -f "demo_server.py" > /dev/null; then
        echo "Stopping demo server..."
        pkill -f "demo_server.py"
    fi
    
    # Find and kill telegram_bot.py
    if pgrep -f "telegram_bot.py" > /dev/null; then
        echo "Stopping Telegram bot..."
        pkill -f "telegram_bot.py"
    fi
    
    # Find and kill uvicorn (FastAPI server)
    if pgrep -f "uvicorn" > /dev/null; then
        echo "Stopping Uvicorn server..."
        pkill -f "uvicorn"
    fi
fi

# Check if any AI Rebuild processes are still running
if pgrep -f "ai.rebuild|demo_server|telegram_bot" > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠ Some processes may still be running${NC}"
    echo "Run 'ps aux | grep -E \"demo_server|telegram_bot\"' to check"
else
    echo -e "${GREEN}✓ All AI Rebuild processes stopped${NC}"
fi

echo ""
echo -e "${GREEN}Shutdown complete${NC}"