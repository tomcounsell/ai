#!/bin/bash

# Telegram Bot Login Script
# This script helps with initial Telegram authentication

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo -e "${BLUE}📱 Telegram Bot Authentication${NC}"
echo "================================"

# Change to project root
cd "$PROJECT_ROOT"

# Check for virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Check for .env file
if [ ! -f ".env" ]; then
    echo -e "${RED}❌ No .env file found${NC}"
    echo "Please create a .env file with:"
    echo "  TELEGRAM_API_ID=your_api_id"
    echo "  TELEGRAM_API_HASH=your_api_hash"
    echo "  TELEGRAM_PHONE=your_phone_number"
    echo "  TELEGRAM_PASSWORD=your_2fa_password (if enabled)"
    exit 1
fi

echo -e "${YELLOW}⚠️  This will start the Telegram authentication process${NC}"
echo -e "${YELLOW}You will need to:${NC}"
echo "  1. Enter the verification code sent to your phone"
echo "  2. Enter your 2FA password if enabled"
echo ""
echo -e "${GREEN}Press Enter to continue or Ctrl+C to cancel...${NC}"
read

# Run the one-time authentication script
echo -e "${BLUE}Starting Telegram authentication...${NC}"
echo ""

python3 scripts/telegram_auth.py

# Check exit code
if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✅ Authentication successful!${NC}"
    echo -e "${GREEN}You can now start the Telegram bot with:${NC}"
    echo -e "${BLUE}  ./scripts/start.sh --telegram${NC}"
else
    echo ""
    echo -e "${RED}❌ Authentication failed${NC}"
    echo "Please check your credentials in .env file"
    exit 1
fi