#!/bin/bash
# Calendar hook for UserPromptSubmit: derives a descriptive slug from the
# user's first prompt via a quick Haiku call, then creates/extends a calendar event.
# Rate-limited: only fires once per 10 minutes (first prompt wins).

set -e

LOCKDIR="$HOME/Desktop/claude_code"
STAMPFILE="$LOCKDIR/.calendar_hook_stamp"
SESSIONFILE="$LOCKDIR/.calendar_hook_session"
SLUGFILE="$LOCKDIR/.calendar_hook_slug"
INTERVAL=600  # 10 minutes in seconds

# Read stdin JSON from Claude Code hook (must happen before rate limit check)
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

# Skip non-billable sessions (updates, setup, config)
if echo "$PROMPT" | grep -qiE '^\s*/(update|setup|clear)|^(update|setup|config)'; then
    exit 0
fi

# Skip excluded projects (too noisy for calendar tracking)
EXCLUDED_PROJECTS="valor"
PROJECTS_JSON="$HOME/src/ai/config/projects.json"
CURRENT_PROJECT=""
if [ -f "$PROJECTS_JSON" ]; then
    CURRENT_PROJECT=$(jq -r --arg cwd "$PWD" '
        .projects | to_entries[]
        | select(.value.working_directory == $cwd)
        | .key
    ' "$PROJECTS_JSON" 2>/dev/null || true)
fi
for excluded in $EXCLUDED_PROJECTS; do
    if [ "$CURRENT_PROJECT" = "$excluded" ]; then
        exit 0
    fi
done

# Rate limit: skip if same session and called within the last INTERVAL seconds
# A new session always bypasses the rate limit
SAME_SESSION=false
if [ -n "$SESSION_ID" ] && [ -f "$SESSIONFILE" ]; then
    PREV_SESSION=$(cat "$SESSIONFILE" 2>/dev/null || echo "")
    if [ "$SESSION_ID" = "$PREV_SESSION" ]; then
        SAME_SESSION=true
    fi
fi

if [ "$SAME_SESSION" = true ] && [ -f "$STAMPFILE" ]; then
    LAST=$(cat "$STAMPFILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    ELAPSED=$((NOW - LAST))
    if [ "$ELAPSED" -lt "$INTERVAL" ]; then
        exit 0
    fi
    # Same session past rate limit: reuse the previously generated slug
    if [ -f "$SLUGFILE" ]; then
        SLUG=$(cat "$SLUGFILE" 2>/dev/null || echo "")
        if [ -n "$SLUG" ]; then
            mkdir -p "$LOCKDIR"
            date +%s > "$STAMPFILE"
            export PATH="$HOME/Library/Python/3.12/bin:$PATH"
            PREV_PROJECT=$(cat "$LOCKDIR/.calendar_hook_project" 2>/dev/null || echo "")
            exec valor-calendar --project "$PREV_PROJECT" "$SLUG"
        fi
    fi
fi

# Resolve project key from projects.json (matches working_directory to key)
# Falls back to directory basename if no match found
PROJECTS_JSON="$HOME/src/ai/config/projects.json"
PROJECT=$(basename "$PWD")
if [ -f "$PROJECTS_JSON" ]; then
    MATCH=$(jq -r --arg cwd "$PWD" '
        .projects | to_entries[]
        | select(.value.working_directory == $cwd)
        | .key
    ' "$PROJECTS_JSON" 2>/dev/null || true)
    if [ -n "$MATCH" ]; then
        PROJECT="$MATCH"
    fi
fi

# Fallback to project name only if no prompt
if [ -z "$PROMPT" ]; then
    SLUG="$PROJECT"
else
    # Load API key
    if [ -f "$(pwd)/.env" ]; then
        ANTHROPIC_API_KEY=$(grep '^ANTHROPIC_API_KEY=' "$(pwd)/.env" | cut -d= -f2-)
    fi
    # Also check shared env
    if [ -z "$ANTHROPIC_API_KEY" ] && [ -f "$HOME/src/.env" ]; then
        ANTHROPIC_API_KEY=$(grep '^ANTHROPIC_API_KEY=' "$HOME/src/.env" | cut -d= -f2-)
    fi
    # Also check ai repo env as final fallback
    if [ -z "$ANTHROPIC_API_KEY" ] && [ -f "$HOME/src/ai/.env" ]; then
        ANTHROPIC_API_KEY=$(grep '^ANTHROPIC_API_KEY=' "$HOME/src/ai/.env" | cut -d= -f2-)
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
                    content: ("Project: " + $project + "\nTask: " + $prompt + "\n\nGenerate a short kebab-case slug (2-4 words) for this calendar event, used for billing and time tracking. Just describe the task — do NOT include the project name as a prefix since the calendar already identifies the project. Output ONLY the slug, nothing else. Examples: auth-bugfix, dashboard-redesign, api-refactor")
                }]
            }')") || true

        SLUG=$(echo "$RESPONSE" | jq -r '.content[0].text // empty' | tr -d '[:space:]' | head -c 60)

        # Validate: must be kebab-case, fallback to project name
        if ! echo "$SLUG" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$'; then
            SLUG="$PROJECT"
        fi
    fi
fi

# Update stamp/session/slug/project and fire calendar event
mkdir -p "$LOCKDIR"
date +%s > "$STAMPFILE"
[ -n "$SESSION_ID" ] && echo "$SESSION_ID" > "$SESSIONFILE"
echo "$SLUG" > "$SLUGFILE"
echo "$PROJECT" > "$LOCKDIR/.calendar_hook_project"
export PATH="$HOME/Library/Python/3.12/bin:$PATH"
exec valor-calendar --project "$PROJECT" "$SLUG"
