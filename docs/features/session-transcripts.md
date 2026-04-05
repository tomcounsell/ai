# Session Transcripts

**Status**: Implemented
**Created**: 2026-02-24

---

## Overview

Session transcripts capture the full content of each agent session — every turn, tool call, and tool result — in append-only `.txt` files on disk. The `AgentSession` Popoto model stores queryable metadata about each session.

This replaces the sparse JSON snapshot approach in `bridge/session_logs.py`.

## Architecture

### Transcript Files

Location: `logs/sessions/{session_id}/transcript.txt`

Each session gets its own directory. Transcript files are **kept indefinitely** — no TTL, no auto-deletion.

Format (one line per event):
```
[2026-02-24T10:30:00.123456] SESSION_START: session_id=abc123 project=valor sender=Tom chat_id=12345
[2026-02-24T10:30:01.234567] USER: Please fix the test failures
[2026-02-24T10:30:02.345678] TOOL_CALL: Read(/Users/valor/src/ai/tests/test_foo.py)
[2026-02-24T10:30:03.456789] TOOL_RESULT: 1→test content here... [truncated]
[2026-02-24T10:30:04.567890] ASSISTANT: I'll fix the failing test by...
[2026-02-24T10:30:05.678901] SESSION_END: status=completed summary=Fixed 3 failing tests
```

Tool results are truncated to 2000 characters in the transcript to keep file sizes manageable.

### JSONL Transcript Backup

Claude Code stores full session transcripts as JSONL files in `~/.claude/projects/-Users-valorengels-src-ai/{uuid}.jsonl`. These contain complete conversation data (`user`, `assistant`, `system`, `progress`, `file-history-snapshot` messages) and are the lossless source of truth — unlike the custom `transcript.txt` format which truncates tool results.

The stop hook (`.claude/hooks/stop.py`) automatically backs up the JSONL transcript on every session stop:

1. Reads `transcript_path` from the hook input (provided by Claude Code)
2. Copies the JSONL file to `logs/sessions/{session_id}/transcript.jsonl`
3. Stores the backup path in `AgentSession.log_path` for later retrieval

This runs unconditionally — the legacy `--chat` flag is accepted but ignored since backup is now always-on. The backup is non-fatal: Redis or model errors are silently caught to avoid breaking the stop hook.

**Why both formats?** The `transcript.txt` is human-readable and written incrementally during the session. The `transcript.jsonl` is a post-session backup of Claude Code's internal format — structured, lossless, and suitable for tooling like [cctrace](https://github.com/jimmc414/cctrace) or [ccexport](https://github.com/marcheiligers/ccexport) for markdown/HTML export.

### AgentSession Model

`models/agent_session.py` — Unified model that replaced both `RedisJob` and `SessionLog`.

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | UniqueKeyField | Unique session identifier |
| `project_key` | KeyField | Project (e.g., "valor", "popoto") |
| `status` | KeyField | active, dormant, completed, failed, abandoned |
| `chat_id` | KeyField | Telegram chat ID |
| `sender` | Field | Who triggered the session |
| `started_at` | SortedField | Unix timestamp, partitioned by project_key |
| `updated_at` | DatetimeField | Last activity timestamp (auto_now=True) |
| `completed_at` | Field | Completion timestamp |
| `turn_count` | IntField | Number of conversation turns |
| `tool_call_count` | IntField | Number of tool calls |
| `log_path` | Field | Path to transcript .txt file |
| `summary` | Field | Brief session outcome summary |
| `branch_name` | Field | Git branch (for tier 2 work items) |
| `slug` | Field | Named work item slug (tier 2) |
| `tags` | ListField | Categorization tags (e.g., "pr-review") |
| `classification_type` | Field | bug, feature, or chore |
| `classification_confidence` | Field | 0.0-1.0 |

**TTL**: Redis metadata expires after 90 days (cleaned by reflections step 13).
**Transcript files**: Kept indefinitely on disk.

## API Reference

```python
from bridge.session_transcript import (
    start_transcript,
    append_turn,
    append_tool_result,
    complete_transcript,
)

# Start a session — creates AgentSession + opens transcript file
log_path = start_transcript(
    session_id="abc123",
    project_key="valor",
    chat_id="12345",
    sender="Tom",
    branch_name="session/fix-tests",
    slug="fix-tests",
)

# Append a conversation turn
append_turn(session_id="abc123", role="user", content="Please fix the tests")
append_turn(session_id="abc123", role="assistant", content="I'll look at the failures")

# Append a tool call (use tool_name parameter)
append_turn(
    session_id="abc123",
    role="tool_call",
    content="",
    tool_name="Read",
    tool_input="/path/to/file.py",
)

# Append a tool result
append_tool_result(session_id="abc123", result="file contents here...")

# Complete the session
complete_transcript(
    session_id="abc123",
    status="completed",
    summary="Fixed 3 failing tests",
)
```

## Integration Points

The session transcript module is integrated at the session lifecycle boundaries in `agent/agent_session_queue.py`:

- **Session start**: `start_transcript()` called when a session begins processing
- **Session end**: `complete_transcript()` called when the session completes or fails

The `AgentSession` model is used everywhere:
- `agent/agent_session_queue.py` - Creates/updates AgentSession at session boundaries
- `agent/sdk_client.py` - Marks session as failed on SDK errors
- `agent/health_check.py` - Updates tool_call_count during sessions
- `monitoring/session_watchdog.py` - Monitors active sessions for health issues
- `bridge/telegram_bridge.py` - Checks for active sessions on reply-to routing

## Session Tagging

The `tags` ListField stores session categorization tags (e.g., "bug", "sdlc", "pr-created", "reflections"). Auto-tagging runs automatically at session completion inside `finalize_session()` in `models/session_lifecycle.py` via `tools/session_tags.py`. The `complete_transcript()` function delegates status mutation and all side effects to `finalize_session()`. See [Session Tagging](session-tagging.md) for the full tagging system documentation.

## Cleanup

```python
from models.agent_session import AgentSession

# Clean up Redis metadata older than 90 days (transcript files preserved)
deleted = AgentSession.cleanup_expired(max_age_days=90)
```

This is called automatically by the reflections maintenance task (step 13: "Redis TTL Cleanup").
