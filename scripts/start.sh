#!/bin/bash

# AI Rebuild System - Startup Script
# This script starts the AI Rebuild system with proper environment setup

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo -e "${GREEN}🚀 AI Rebuild System Startup${NC}"
echo "================================"

# Change to project root
cd "$PROJECT_ROOT"

# Check Python installation
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 is not installed${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Python found: $(python3 --version)"

# Check for virtual environment
if [ -d "venv" ]; then
    echo -e "${GREEN}✓${NC} Activating virtual environment"
    source venv/bin/activate
elif [ -d ".venv" ]; then
    echo -e "${GREEN}✓${NC} Activating virtual environment"
    source .venv/bin/activate
else
    echo -e "${YELLOW}⚠${NC} No virtual environment found, using system Python"
fi

# Check for .env file
if [ -f ".env" ]; then
    echo -e "${GREEN}✓${NC} Environment file found"
    # Load .env file, but only export valid bash variables
    # Skip lines with special characters that aren't valid in bash
    set -a  # Mark variables for export
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        [[ "$key" =~ ^#.*$ ]] && continue
        [[ -z "$key" ]] && continue
        # Only export if key is a valid bash variable name (alphanumeric and underscore)
        if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            eval "export $key=\"$value\""
        fi
    done < .env
    set +a  # Stop marking for export
else
    echo -e "${YELLOW}⚠${NC} No .env file found - using defaults"
fi

# Create required directories
echo "Creating required directories..."
mkdir -p data logs temp data/backups data/monitoring data/process_state

# Parse command line arguments
MODE="production"
VERBOSE=""
DRY_RUN=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --demo)
            MODE="demo"
            shift
            ;;
        --telegram)
            MODE="telegram"
            shift
            ;;
        --verbose)
            VERBOSE="--verbose"
            shift
            ;;
        --dry-run)
            DRY_RUN="--dry-run"
            shift
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --demo      Start demo server (no API keys required)"
            echo "  --telegram  Start Telegram bot"
            echo "  --verbose   Enable verbose logging"
            echo "  --dry-run   Validate configuration without starting"
            echo "  --help      Show this help message"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Start the appropriate server
case $MODE in
    demo)
        echo -e "${GREEN}Starting Demo Server...${NC}"
        echo "Visit http://localhost:8000 in your browser"
        echo "Press Ctrl+C to stop the server"
        echo ""
        python3 demo_server.py
        ;;
    telegram)
        echo -e "${GREEN}Starting Telegram Bot...${NC}"
        echo -e "${YELLOW}Note: If 2FA is enabled, you will be prompted for your authentication code${NC}"
        echo "Press Ctrl+C to stop the bot"
        echo ""
        # Use exec for Telegram to ensure input is available for 2FA
        exec python3 telegram_bot.py
        ;;
    production)
        echo -e "${GREEN}Starting Production Server...${NC}"
        echo ""
        python3 scripts/startup.py $VERBOSE $DRY_RUN
        ;;
esac