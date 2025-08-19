#!/bin/bash

# AI Rebuild - Telegram Bot Runner
# One command to authenticate (if needed), start, and tail logs

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo -e "${BLUE}🤖 AI Rebuild Telegram Bot Runner${NC}"
echo "================================"

# Activate virtual environment if exists
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Check authentication status
echo -e "${YELLOW}Checking Telegram authentication...${NC}"
if python3 scripts/telegram_auth.py 2>&1 | grep -q "Successfully authenticated"; then
    echo -e "${GREEN}✅ Already authenticated${NC}"
else
    echo -e "${YELLOW}📱 Need to authenticate with Telegram${NC}"
    python3 scripts/telegram_auth.py
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Authentication failed${NC}"
        exit 1
    fi
fi

# Start the Telegram bot in background
echo -e "${BLUE}Starting Telegram bot...${NC}"
python3 telegram_bot.py > logs/telegram_bot.log 2>&1 &
BOT_PID=$!
echo -e "${GREEN}✅ Bot started (PID: $BOT_PID)${NC}"

# Give it a moment to start
sleep 2

# Tail the logs
echo -e "${BLUE}📋 Tailing logs (Ctrl+C to stop)...${NC}"
echo "================================"
exec ./scripts/logs.sh --telegram