# Session Health Check (Watchdog Hook)

PostToolUse hook that monitors agent sessions for stuck loops using a Haiku judge.

## Overview

The watchdog hook (`agent/health_check.py`) fires after every tool call in a Claude Code subprocess. Every `CHECK_INTERVAL` (20) tool calls, it reads the recent activity transcript and asks Haiku whether the agent is making meaningful progress or is stuck in a repetitive loop.

If judged unhealthy, the watchdog:
1. Sets `watchdog_unhealthy` on the AgentSession model (so the nudge loop delivers output instead of auto-continuing)
2. Injects a STOP directive via `additionalContext` (so Claude sees the alert immediately)

## Data Flow

1. **PostToolUse fires** after every tool call
2. **`_summarize_input()`** extracts tool name + key args (including offset/limit for Read, old_string length for Edit)
3. **`_write_activity_stream()`** appends to `logs/sessions/{session_id}/activity.jsonl`
4. **At CHECK_INTERVAL**: `_read_recent_activity()` reads last 20 entries from the transcript
5. **`_get_session_context()`** queries AgentSession for metadata + computes activity statistics
6. **`JUDGE_PROMPT`** formats context + activity + pattern guidance into a prompt
7. **`_judge_health()`** sends to Haiku, parses JSON verdict
8. **Output**: If unhealthy, flags session and injects stop directive

## Context Enrichment

The judge receives rich context to reduce false positives:

### Tool Summaries

`_summarize_input()` includes distinguishing parameters:
- **Read**: `file.py [offset=200, limit=100]` (chunked reads are not loops)
- **Edit**: `file.py [old_string len=45]` (edit size context)
- **Bash**: First 120 chars of command
- **Grep/Glob**: Pattern string

### Session Statistics

`_get_session_context()` computes from the activity stream:
- **Tool distribution**: Count by tool name (e.g., "5 Read, 3 Edit, 12 Bash")
- **Total tool call count**: From in-memory counter
- **Commit count**: Bash entries containing "git commit"
- **GitHub CLI commands**: Recent `gh` commands for PM session context

### Pattern Guidance

`JUDGE_PROMPT` includes guidance on legitimate patterns:
- Chunked reads of the same file with different offsets
- Read-then-edit cycles on the same file
- Setup tools (ToolSearch, Skill) early in a session
- Sessions with git commits are making real progress
- High tool counts (50+) are normal for build sessions

## Steering

The hook also checks a Redis steering queue on every tool call (lightweight LPOP). Steering messages can:
- **Abort**: Inject a stop directive immediately
- **Redirect**: Inject supervisor messages into the agent's context

## Key Functions

| Function | Purpose |
|----------|---------|
| `watchdog_hook()` | Main PostToolUse hook entry point |
| `_summarize_input()` | Brief summary of tool input for logging |
| `_write_activity_stream()` | Append JSONL activity entry |
| `_get_session_context()` | Build context preamble with stats |
| `_compute_activity_stats()` | Tool distribution + commit count from JSONL |
| `_judge_health()` | Send prompt to Haiku, parse verdict |
| `_set_unhealthy()` | Flag session on AgentSession model |
| `is_session_unhealthy()` | Check flag (called by nudge loop) |
| `clear_unhealthy()` | Clear flag on session restart |
| `reset_session_count()` | Reset tool counter for fresh sessions |

## Configuration

| Constant | Value | Description |
|----------|-------|-------------|
| `CHECK_INTERVAL` | 20 | Health check fires every N tool calls |

## Related

- [agent-session-health-monitor.md](agent-session-health-monitor.md) -- Queue-level health monitoring (complementary layer)
- [bridge-self-healing.md](bridge-self-healing.md) -- Bridge process-level crash recovery
- `agent/health_check.py` -- Implementation source
- `tests/unit/test_health_check.py` -- Unit tests
- Issue #625 -- Context enrichment tracking issue
- Issue #374 -- Prior fix for stale session counts
