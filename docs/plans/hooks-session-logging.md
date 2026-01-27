# Plan: Hooks System + Session Logging

## Overview

Add Claude Code hooks to capture session events and enable structured logging for debugging and analytics.

## Source Inspiration

From `indydan/tac-6/.claude/settings.json` and `.claude/hooks/` directory.

## Problem Statement

Currently, Valor has no visibility into:
- What tools are being called during sessions
- When sessions start/stop
- Full conversation transcripts for debugging
- Subagent activity

This makes debugging Telegram bridge issues difficult.

## Proposed Solution

Add a hooks system that logs session events to structured files.

### New Files to Create

```
.claude/
  hooks/
    pre_tool_use.py      # Log before tool execution
    post_tool_use.py     # Log after tool execution
    stop.py              # Save transcript on session end
    subagent_stop.py     # Track subagent completions
    notification.py      # Log notifications
    utils/
      constants.py       # Shared utilities (ensure_session_log_dir)
```

### Settings Changes

Update `.claude/settings.local.json` to add:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python $CLAUDE_PROJECT_DIR/.claude/hooks/pre_tool_use.py || true"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python $CLAUDE_PROJECT_DIR/.claude/hooks/post_tool_use.py || true"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python $CLAUDE_PROJECT_DIR/.claude/hooks/stop.py --chat || true"
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python $CLAUDE_PROJECT_DIR/.claude/hooks/subagent_stop.py || true"
          }
        ]
      }
    ]
  }
}
```

### Log Structure

```
logs/
  sessions/
    {session_id}/
      tool_use.json       # All tool calls with timing
      stop.json           # Session end metadata
      chat.json           # Full transcript (from Stop hook)
      subagents.json      # Subagent activity
```

## Implementation Steps

1. Create `.claude/hooks/utils/constants.py` with `ensure_session_log_dir()`
2. Create `pre_tool_use.py` - reads JSON from stdin, logs tool name/params
3. Create `post_tool_use.py` - logs tool completion and timing
4. Create `stop.py` - saves session metadata and optionally copies transcript
5. Create `subagent_stop.py` - tracks subagent completions
6. Update `settings.local.json` with hooks configuration
7. Add `logs/sessions/` to `.gitignore`

## Benefits

- Debug Telegram bridge issues by reviewing session logs
- Track tool usage patterns
- Preserve conversation history for analysis
- Monitor subagent activity

## Estimated Effort

Medium - 4-6 files, straightforward Python scripts

## Dependencies

None - uses only standard library

## Risks

- Hooks run on every tool call, could impact performance
- Log files could grow large without rotation
- Need to ensure hooks fail gracefully (use `|| true`)
