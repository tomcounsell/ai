#!/bin/bash

# MCP Configuration Update Script
# Updates .mcp.json based on available API keys in .env

set -e

PROJECT_ROOT="$(dirname "$0")/.."
ENV_FILE="$PROJECT_ROOT/.env"
MCP_FILE="$PROJECT_ROOT/.mcp.json"

# Function to load environment variables
load_env() {
    if [ ! -f "$ENV_FILE" ]; then
        echo "Error: .env file not found at $ENV_FILE"
        echo "Please copy .env.sample to .env and add your API keys"
        exit 1
    fi

    # Source the .env file to load variables
    set -a  # automatically export all variables
    source "$ENV_FILE"
    set +a  # stop auto-exporting
}

# Function to check if a variable is set and not empty
is_api_key_available() {
    local var_name="$1"
    local var_value="${!var_name}"

    if [ -n "$var_value" ] && [ "$var_value" != "" ] && [[ "$var_value" != *"****"* ]]; then
        return 0  # true
    else
        return 1  # false
    fi
}

# Function to create/update MCP configuration
update_mcp_config() {
    echo "Updating MCP configuration..."

    # Start building the JSON configuration
    local mcp_config='{"mcpServers":{}}'

    # Check for Notion API key and add to config
    if is_api_key_available "NOTION_API_KEY"; then
        echo "✓ Adding Notion MCP server"
        mcp_config=$(echo "$mcp_config" | jq '.mcpServers.notionApi = {
            "command": "npx",
            "args": ["-y", "@notionhq/notion-mcp-server"],
            "env": {
                "OPENAPI_MCP_HEADERS": "{\"Authorization\": \"Bearer '"$NOTION_API_KEY"'\", \"Notion-Version\": \"2022-06-28\"}"
            }
        }')
    else
        echo "⚠ Notion API key not found or placeholder - skipping Notion MCP"
    fi

    # Add more MCP servers here as needed
    # Example for future additions:
    # if is_api_key_available "OPENAI_API_KEY"; then
    #     echo "✓ Adding OpenAI MCP server"
    #     # Add OpenAI MCP configuration
    # fi

    # Write the configuration to .mcp.json
    echo "$mcp_config" | jq '.' > "$MCP_FILE"
    echo "MCP configuration written to $MCP_FILE"
}

# Function to display current configuration
show_config() {
    if [ -f "$MCP_FILE" ]; then
        echo ""
        echo "Current MCP configuration:"
        cat "$MCP_FILE" | jq '.'
    fi
}

# Main execution
main() {
    echo "MCP Configuration Update Script"
    echo "================================"

    # Check if jq is available
    if ! command -v jq &> /dev/null; then
        echo "Error: jq is required but not installed"
        echo "Install with: brew install jq (macOS) or apt-get install jq (Ubuntu)"
        exit 1
    fi

    cd "$PROJECT_ROOT" || exit 1

    load_env
    update_mcp_config
    show_config

    echo ""
    echo "✅ MCP configuration update complete!"
    echo "You can now use the configured MCP servers with Claude Code"
}

# Run main function
main "$@"
