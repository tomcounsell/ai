#!/bin/bash
# Stop calendar heartbeat hook (thin wrapper).
#
# Extends the current feature's calendar event at session-stop. All logic lives
# in tools/valor_calendar.py behind `valor-calendar --hook --event stop`; this
# wrapper forwards the Claude Code hook JSON (stdin) and returns immediately.
#
# See docs/features/google-calendar-integration.md.

set +e  # a calendar hook must never fail the session

REPO_DIR="${CLAUDE_PROJECT_DIR:-$HOME/src/ai}"
CAL="$REPO_DIR/.venv/bin/valor-calendar"
[ ! -x "$CAL" ] && CAL="$(command -v valor-calendar 2>/dev/null)"
[ -z "$CAL" ] && exit 0

INPUT="$(cat)"
( printf '%s' "$INPUT" | "$CAL" --hook --event stop >/dev/null 2>&1 ) &
exit 0
