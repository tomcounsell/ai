#!/bin/bash

# AI Rebuild System - Log Viewer
# This script tails logs for monitoring the running system

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

# Default options
LOG_TYPE="all"
LINES=50
FOLLOW=true

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --startup)
            LOG_TYPE="startup"
            shift
            ;;
        --main)
            LOG_TYPE="main"
            shift
            ;;
        --telegram)
            LOG_TYPE="telegram"
            shift
            ;;
        --errors)
            LOG_TYPE="errors"
            shift
            ;;
        -n|--lines)
            LINES="$2"
            shift 2
            ;;
        --no-follow)
            FOLLOW=false
            shift
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --main      Show main application logs"
            echo "  --startup   Show startup logs"
            echo "  --telegram  Show Telegram-related logs"
            echo "  --errors    Show only error messages"
            echo "  -n, --lines Number of lines to show initially (default: 50)"
            echo "  --no-follow Don't follow log updates"
            echo "  --help      Show this help message"
            echo ""
            echo "Default: Shows all logs with live updates"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Function to show header
show_header() {
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}📋 AI Rebuild System - Log Viewer${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Function to show logs
show_logs() {
    local log_files=""
    local grep_pattern=""
    local description=""
    
    case $LOG_TYPE in
        all)
            log_files="logs/*.log"
            description="All Logs"
            ;;
        main)
            log_files="logs/ai_rebuild.log"
            description="Main Application Log"
            ;;
        startup)
            log_files="logs/startup.log"
            description="Startup Log"
            ;;
        telegram)
            log_files="logs/*.log"
            grep_pattern="telegram|Telegram|TELEGRAM"
            description="Telegram Logs"
            ;;
        errors)
            log_files="logs/*.log"
            grep_pattern="ERROR|CRITICAL|Failed|failed|Error|error"
            description="Error Logs"
            ;;
    esac
    
    show_header
    echo -e "${YELLOW}Viewing: ${description}${NC}"
    echo -e "${YELLOW}Lines: ${LINES} | Follow: ${FOLLOW}${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    
    # Build the tail command
    local tail_cmd="tail"
    
    if [ "$FOLLOW" = true ]; then
        tail_cmd="$tail_cmd -f"
    fi
    
    tail_cmd="$tail_cmd -n $LINES"
    
    # Check if log files exist
    if ! ls $log_files 1> /dev/null 2>&1; then
        echo -e "${RED}❌ No log files found matching: $log_files${NC}"
        echo -e "${YELLOW}Make sure the system has been started at least once.${NC}"
        exit 1
    fi
    
    # Execute the tail command with optional grep
    if [ -n "$grep_pattern" ]; then
        if [ "$FOLLOW" = true ]; then
            echo -e "${BLUE}Following logs... Press Ctrl+C to stop${NC}"
            echo ""
            $tail_cmd $log_files | grep -E "$grep_pattern" --color=always
        else
            $tail_cmd $log_files | grep -E "$grep_pattern" --color=always
        fi
    else
        if [ "$FOLLOW" = true ]; then
            echo -e "${BLUE}Following logs... Press Ctrl+C to stop${NC}"
            echo ""
        fi
        $tail_cmd $log_files
    fi
}

# Create logs directory if it doesn't exist
mkdir -p logs

# Show the logs
show_logs