#!/bin/bash
# Calendar hook for UserPromptSubmit: derives a descriptive slug from the
# user's first prompt via a quick Haiku call, then creates/extends a calendar event.
# Rate-limited: only fires once per 10 minutes (first prompt wins).

set +e  # Hooks must never fail noisily

LOCKDIR="$HOME/Desktop/Valor"
STAMPFILE="$LOCKDIR/.calendar_hook_stamp"
SESSIONFILE="$LOCKDIR/.calendar_hook_session"
SLUGFILE="$LOCKDIR/.calendar_hook_slug"
INTERVAL=600  # 10 minutes in seconds

# Read stdin JSON from Claude Code hook (must happen before rate limit check)
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

# Skip bare slash commands (no extra prompt = no real work)
# e.g. "/update" skips, but "/do-build implement auth" tracks
if echo "$PROMPT" | grep -qE '^\s*/[a-zA-Z0-9_-]+\s*$'; then
    exit 0
fi

# Only track projects that have a calendar mapping (allowlist via calendar_config.json)
PROJECTS_JSON="${PROJECTS_CONFIG_PATH:-$HOME/Desktop/Valor/projects.json}"
CALENDAR_JSON="$HOME/Desktop/Valor/calendar_config.json"
CURRENT_PROJECT=""
if [ -f "$PROJECTS_JSON" ]; then
    CURRENT_PROJECT=$(jq -r --arg cwd "$PWD" --arg home "$HOME" '
        .projects | to_entries[]
        | select((.value.working_directory | gsub("^~"; $home)) == $cwd)
        | .key
    ' "$PROJECTS_JSON" 2>/dev/null || true)
fi
# Skip if project not in calendar_config.json
if [ -z "$CURRENT_PROJECT" ]; then
    exit 0
fi
if [ -f "$CALENDAR_JSON" ]; then
    HAS_CALENDAR=$(jq -r --arg proj "$CURRENT_PROJECT" '.calendars[$proj] // empty' "$CALENDAR_JSON" 2>/dev/null || true)
    if [ -z "$HAS_CALENDAR" ]; then
        exit 0
    fi
else
    exit 0
fi

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
            export PATH="$HOME/src/ai/.venv/bin:$HOME/Library/Python/3.12/bin:$PATH"
            PREV_PROJECT=$(cat "$LOCKDIR/.calendar_hook_project" 2>/dev/null || echo "")
            valor-calendar --project "$PREV_PROJECT" "$SLUG" 2>/dev/null || true
            exit 0
        fi
    fi
fi

# Resolve project key from projects.json (matches working_directory to key)
# Falls back to directory basename if no match found
PROJECTS_JSON="${PROJECTS_CONFIG_PATH:-$HOME/Desktop/Valor/projects.json}"
PROJECT=$(basename "$PWD")
if [ -f "$PROJECTS_JSON" ]; then
    MATCH=$(jq -r --arg cwd "$PWD" --arg home "$HOME" '
        .projects | to_entries[]
        | select((.value.working_directory | gsub("^~"; $home)) == $cwd)
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
        # Read model ID from central config (single source of truth)
        HAIKU_MODEL=$(grep '^HAIKU = ' "$HOME/src/ai/config/models.py" | sed 's/.*"\(.*\)"/\1/' 2>/dev/null)
        HAIKU_MODEL="${HAIKU_MODEL:-claude-haiku-4-5-20251001}"

        # Call Haiku to derive a short billing slug from the prompt
        RESPONSE=$(curl -s --max-time 5 https://api.anthropic.com/v1/messages \
            -H "x-api-key: $ANTHROPIC_API_KEY" \
            -H "anthropic-version: 2023-06-01" \
            -H "content-type: application/json" \
            -d "$(jq -n --arg project "$PROJECT" --arg prompt "$PROMPT" --arg model "$HAIKU_MODEL" '{
                model: $model,
                max_tokens: 30,
                messages: [{
                    role: "user",
                    content: ("Project: " + $project + "\nTask: " + $prompt + "\n\nGenerate a short kebab-case slug (2-4 words) for this calendar event, used for billing and time tracking. Just describe the task — do NOT include the project name as a prefix since the calendar already identifies the project. Output ONLY the slug, nothing else. Examples: auth-bugfix, dashboard-redesign, api-refactor")
                }]
            }')") || true

        # Check for API error (model retired, auth failure, etc.)
        API_ERROR=$(echo "$RESPONSE" | jq -r '.error.message // empty')
        if [ -n "$API_ERROR" ]; then
            echo "calendar_prompt_hook: API error: $API_ERROR" >&2
        fi

        SLUG=$(echo "$RESPONSE" | jq -r '.content[0].text // empty' | tr -d '[:space:]' | head -c 60)

        # Validate: must be kebab-case, fallback to project name
        if ! echo "$SLUG" | grep -qE '^[a-z0-9][a-z0-9-]*[a-z0-9]$'; then
            [ -z "$API_ERROR" ] && echo "calendar_prompt_hook: invalid slug '$SLUG', falling back to project name" >&2
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
export PATH="$HOME/src/ai/.venv/bin:$HOME/Library/Python/3.12/bin:$PATH"
valor-calendar --project "$PROJECT" "$SLUG" 2>/dev/null || true
