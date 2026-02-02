#!/bin/bash
# Calendar heartbeat hook for Claude Code sessions.
# Rate-limited: only calls valor-calendar if 25+ minutes since last call.
# Reads project slug from directory name of cwd passed via stdin JSON.

set -e

LOCKDIR="$HOME/Desktop/claude_code"
STAMPFILE="$LOCKDIR/.calendar_hook_stamp"
INTERVAL=1500  # 25 minutes in seconds

# Derive slug from current working directory name
SLUG="$(basename "$PWD")"

# Rate limit: skip if called within the last INTERVAL seconds
if [ -f "$STAMPFILE" ]; then
    LAST=$(cat "$STAMPFILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    ELAPSED=$((NOW - LAST))
    if [ "$ELAPSED" -lt "$INTERVAL" ]; then
        exit 0
    fi
fi

# Update stamp and fire heartbeat
date +%s > "$STAMPFILE"
export PATH="$HOME/Library/Python/3.12/bin:$PATH"
exec valor-calendar "$SLUG"
