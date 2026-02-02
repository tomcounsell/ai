#!/bin/bash
# Calendar hook for UserPromptSubmit: derives a descriptive slug from the
# user's first prompt via a quick Haiku call, then creates/extends a calendar event.
# Rate-limited: only fires once per 25 minutes (first prompt wins).

set -e

LOCKDIR="$HOME/Desktop/claude_code"
STAMPFILE="$LOCKDIR/.calendar_hook_stamp"
INTERVAL=1500  # 25 minutes in seconds

# Rate limit: skip if called within the last INTERVAL seconds
if [ -f "$STAMPFILE" ]; then
    LAST=$(cat "$STAMPFILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    ELAPSED=$((NOW - LAST))
    if [ "$ELAPSED" -lt "$INTERVAL" ]; then
        exit 0
    fi
fi

# Read stdin JSON from Claude Code hook
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')
PROJECT=$(basename "$PWD")

# Fallback to directory name if no prompt
if [ -z "$PROMPT" ]; then
    SLUG="$PROJECT"
else
    # Load API key
    if [ -f "$(pwd)/.env" ]; then
        ANTHROPIC_API_KEY=$(grep '^ANTHROPIC_API_KEY=' "$(pwd)/.env" | cut -d= -f2-)
    fi
    if [ -z "$ANTHROPIC_API_KEY" ]; then
        SLUG="$PROJECT"
    else
        # Call Haiku to derive a short billing slug from the prompt
        RESPONSE=$(curl -s --max-time 5 https://api.anthropic.com/v1/messages \
            -H "x-api-key: $ANTHROPIC_API_KEY" \
            -H "anthropic-version: 2023-06-01" \
            -H "content-type: application/json" \
            -d "$(jq -n --arg project "$PROJECT" --arg prompt "$PROMPT" '{
                model: "claude-3-5-haiku-20241022",
                max_tokens: 30,
                messages: [{
                    role: "user",
                    content: ("Project: " + $project + "\nTask: " + $prompt + "\n\nGenerate a short kebab-case slug (2-4 words) for this task suitable for a calendar event and billing. Include the project name as prefix. Output ONLY the slug, nothing else. Example: myproject-auth-bugfix")
                }]
            }')") || true

        SLUG=$(echo "$RESPONSE" | jq -r '.content[0].text // empty' | tr -d '[:space:]' | head -c 60)

        # Validate: must be kebab-case, fallback to project name
        if ! echo "$SLUG" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$'; then
            SLUG="$PROJECT"
        fi
    fi
fi

# Update stamp and fire calendar event
mkdir -p "$LOCKDIR"
date +%s > "$STAMPFILE"
export PATH="$HOME/Library/Python/3.12/bin:$PATH"
exec valor-calendar "$SLUG"
