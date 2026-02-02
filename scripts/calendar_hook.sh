#!/bin/bash
# Calendar heartbeat hook for Claude Code sessions.
# Rate-limited: only calls valor-calendar if 10+ minutes since last call.
# Reads project slug from directory name of cwd passed via stdin JSON.

set -e

LOCKDIR="$HOME/Desktop/claude_code"
STAMPFILE="$LOCKDIR/.calendar_hook_stamp"
SESSIONFILE="$LOCKDIR/.calendar_hook_session"
INTERVAL=600  # 10 minutes in seconds

# Read stdin JSON from Claude Code hook
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

# Resolve project name from projects.json (matches working_directory to name)
PROJECTS_JSON="$HOME/src/ai/config/projects.json"
SLUG=$(basename "$PWD")
if [ -f "$PROJECTS_JSON" ]; then
    MATCH=$(jq -r --arg cwd "$PWD" '
        .projects | to_entries[]
        | select(.value.working_directory == $cwd)
        | .value.name // empty
    ' "$PROJECTS_JSON" 2>/dev/null || true)
    if [ -n "$MATCH" ]; then
        SLUG="$MATCH"
    fi
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
fi

# Update stamp/session and fire heartbeat
mkdir -p "$LOCKDIR"
date +%s > "$STAMPFILE"
[ -n "$SESSION_ID" ] && echo "$SESSION_ID" > "$SESSIONFILE"
export PATH="$HOME/Library/Python/3.12/bin:$PATH"
exec valor-calendar "$SLUG"
