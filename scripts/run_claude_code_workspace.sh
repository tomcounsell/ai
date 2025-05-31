#!/bin/bash

# Script to run Claude Code in a specific workspace directory
# Usage: scripts/run_claude_code_workspace.sh <workspace_name> <prompt>

set -e

# Check if arguments provided
if [ $# -lt 2 ]; then
    echo "Usage: $0 <workspace_name> <prompt>"
    echo "Example: $0 'DeckFusion Dev' 'Fix the login bug'"
    exit 1
fi

WORKSPACE_NAME="$1"
PROMPT="$2"

# Path to the workspace configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$AI_DIR/config/workspace_config.json"

# Check if config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Workspace configuration file not found at $CONFIG_FILE"
    exit 1
fi

# Extract working directory for the workspace using Python
WORKING_DIR=$(python3 -c "
import json
import sys

try:
    with open('$CONFIG_FILE') as f:
        config = json.load(f)
    
    workspaces = config.get('workspaces', {})
    if '$WORKSPACE_NAME' in workspaces:
        working_dir = workspaces['$WORKSPACE_NAME'].get('working_directory')
        if working_dir:
            print(working_dir)
        else:
            print('ERROR: No working_directory configured for workspace $WORKSPACE_NAME', file=sys.stderr)
            sys.exit(1)
    else:
        print('ERROR: Workspace $WORKSPACE_NAME not found', file=sys.stderr)
        sys.exit(1)
except Exception as e:
    print(f'ERROR: Failed to parse config: {e}', file=sys.stderr)
    sys.exit(1)
")

# Check if we got a valid working directory
if [[ "$WORKING_DIR" == ERROR:* ]]; then
    echo "$WORKING_DIR" >&2
    exit 1
fi

# Verify the working directory exists
if [ ! -d "$WORKING_DIR" ]; then
    echo "Error: Working directory does not exist: $WORKING_DIR"
    exit 1
fi

echo "🏢 Workspace: $WORKSPACE_NAME"
echo "📁 Working Directory: $WORKING_DIR"
echo "💬 Prompt: $PROMPT"
echo "---"

# Change to working directory and run Claude Code
cd "$WORKING_DIR"

# Execute Claude Code with the prompt
echo "🤖 Running Claude Code in $WORKING_DIR..."
claude code "$PROMPT"

echo "✅ Claude Code execution completed"