#!/bin/bash
#
# Telegram Client Authorization Script
#
# This script handles the interactive authorization process for the Telegram bot.
# It creates a session file that allows the bot to connect without re-authorization.
#
# Requirements:
#   - TELEGRAM_API_ID and TELEGRAM_API_HASH in .env file
#   - Phone number access for receiving verification code
#   - Internet connection
#
# Usage:
#   ./scripts/telegram_login.sh
#

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo -e "${BLUE}🤖 Telegram Bot Authorization${NC}"
echo "================================"
echo ""

# Check if we're in the right directory
if [[ ! -f "$PROJECT_ROOT/requirements.txt" ]]; then
    echo -e "${RED}❌ Error: Could not find project root${NC}"
    echo "Please run this script from the project directory"
    exit 1
fi

# Change to project directory
cd "$PROJECT_ROOT"

# Check if .env file exists
if [[ ! -f ".env" ]]; then
    echo -e "${YELLOW}⚠️  Warning: .env file not found${NC}"
    echo "Please create a .env file with your Telegram API credentials:"
    echo ""
    echo "TELEGRAM_API_ID=your_api_id"
    echo "TELEGRAM_API_HASH=your_api_hash"
    echo ""
    echo "Get these from: https://my.telegram.org/apps"
    exit 1
fi

# Check if required environment variables are set
echo "🔍 Checking environment variables..."

# Source the .env file to check variables
set -a  # Mark all new/modified variables for export
source .env
set +a  # Stop marking variables for export

if [[ -z "$TELEGRAM_API_ID" ]]; then
    echo -e "${RED}❌ TELEGRAM_API_ID not found in .env file${NC}"
    exit 1
fi

if [[ -z "$TELEGRAM_API_HASH" ]]; then
    echo -e "${RED}❌ TELEGRAM_API_HASH not found in .env file${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Environment variables found${NC}"
echo ""

# Check if Python is available
if ! command -v python &> /dev/null; then
    echo -e "${RED}❌ Python not found${NC}"
    echo "Please install Python or activate your virtual environment"
    exit 1
fi

# Check if required Python packages are available
echo "🔍 Checking Python dependencies..."
python -c "import pyrogram, dotenv" 2>/dev/null || {
    echo -e "${YELLOW}⚠️  Installing missing dependencies...${NC}"

    if command -v uv &> /dev/null; then
        echo "Using uv to install dependencies..."
        uv pip install -r requirements.txt
    elif command -v pip &> /dev/null; then
        echo "Using pip to install dependencies..."
        pip install -r requirements.txt
    else
        echo -e "${RED}❌ Neither uv nor pip found${NC}"
        echo "Please install dependencies manually:"
        echo "  pip install -r requirements.txt"
        exit 1
    fi
}

echo -e "${GREEN}✅ Dependencies ready${NC}"
echo ""

# Run the authorization script
echo -e "${BLUE}🚀 Starting Telegram authorization...${NC}"
echo ""
echo "This will:"
echo "1. Connect to Telegram servers"
echo "2. Request your phone number"
echo "3. Send you a verification code"
echo "4. Create a session file for future use"
echo ""

# Run the Python authorization script
if python integrations/telegram/auth.py; then
    echo ""
    echo -e "${GREEN}🎉 Authorization successful!${NC}"
    echo ""
    echo "You can now run the Telegram bot with:"
    echo -e "  ${BLUE}uv run agents/telegram_chat_agent.py${NC}"
    echo -e "  ${BLUE}scripts/start.sh${NC}"
    echo ""
    echo "The session file has been saved and will be used automatically."
    exit 0
else
    echo ""
    echo -e "${RED}❌ Authorization failed!${NC}"
    echo ""
    echo "Common solutions:"
    echo "- Check your internet connection"
    echo "- Verify API credentials in .env file"
    echo "- Ensure phone number is in international format (+1234567890)"
    echo "- Try again if verification code expired"
    echo ""
    echo "For API credentials, visit: https://my.telegram.org/apps"
    exit 1
fi
