#!/bin/bash
# UserPromptSubmit calendar hook (thin wrapper).
#
# All logic — project resolution, feature-key coalescing, client-facing slug
# naming, trivial-prompt gating, rate-limiting, and day-bounded events — lives
# in tools/valor_calendar.py behind `valor-calendar --hook`. This wrapper just
# forwards the Claude Code hook JSON (stdin) and returns immediately; the work
# runs detached so calendar logging never delays the user's prompt.
#
# See docs/features/calendar-work-logging.md.

set +e  # a calendar hook must never fail the session

REPO_DIR="${CLAUDE_PROJECT_DIR:-$HOME/src/ai}"
CAL="$REPO_DIR/.venv/bin/valor-calendar"
[ ! -x "$CAL" ] && CAL="$(command -v valor-calendar 2>/dev/null)"
[ -z "$CAL" ] && exit 0

INPUT="$(cat)"
# Detach: redirect the child's std streams off the hook pipe so Claude Code
# doesn't wait on it, and run in the background.
( printf '%s' "$INPUT" | "$CAL" --hook --event prompt >/dev/null 2>&1 ) &
exit 0
