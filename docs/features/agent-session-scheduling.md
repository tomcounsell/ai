# Session Self-Scheduling: Agent-Initiated Queue Operations

**Status**: Shipped

## Overview

The agent can programmatically schedule SDLC runs for GitHub issues, enqueue arbitrary Q&A sessions, and manage queue state -- all mid-conversation via the `tools/agent_session_scheduler.py` CLI tool.

## Usage

### Schedule SDLC Work for a GitHub Issue

```bash
python -m tools.agent_session_scheduler schedule --issue 113
python -m tools.agent_session_scheduler schedule --issue 113 --priority high
python -m tools.agent_session_scheduler schedule --issue 113 --after "2026-03-12T02:00:00Z"
```

The tool validates the issue exists via `gh issue view`, constructs an SDLC dispatch message, and creates an `AgentSession` directly in Redis via Popoto ORM.

### Push Arbitrary Message Jobs

```bash
python -m tools.agent_session_scheduler push --message "What is the architecture?" --project valor
python -m tools.agent_session_scheduler push --message "Fix the bug" --priority high
```

### Queue Inspection

```bash
python -m tools.agent_session_scheduler status
python -m tools.agent_session_scheduler status --project valor
```

### Queue Manipulation

```bash
python -m tools.agent_session_scheduler bump --agent-session-id <ID>    # Move to top (priority=urgent)
python -m tools.agent_session_scheduler pop --project valor              # Remove next without executing
python -m tools.agent_session_scheduler cancel --agent-session-id <ID>   # Cancel specific session
```

### Session Listing and Cleanup

```bash
# List sessions by status (comma-separated)
python -m tools.agent_session_scheduler list --status killed,abandoned
python -m tools.agent_session_scheduler list --status completed --limit 5

# Clean up stale sessions (killed/abandoned/failed older than N minutes)
python -m tools.agent_session_scheduler cleanup --age 30 --dry-run   # Preview what would be deleted
python -m tools.agent_session_scheduler cleanup --age 30              # Actually delete
python -m tools.agent_session_scheduler cleanup --age 60 --project valor  # Scope to one project
```

The `cleanup` command deletes sessions in terminal statuses (`killed`, `abandoned`, `failed`) that are older than the specified age. Uses `delete()` directly — not status mutation — to avoid creating orphaned records (see KeyField index note in [Agent Session Queue](agent-session-queue.md)).

## Architecture

### Tool, Not MCP Server

The agent runs inside Claude Code with Bash access. A Python CLI tool (`tools/agent_session_scheduler.py`) is simpler than an MCP server. The agent calls it via `python -m tools.agent_session_scheduler schedule --issue 113`.

### Redis via Popoto (Direct Write)

The tool writes `AgentSession` objects directly to Redis using Popoto ORM. The bridge worker polls Redis -- no IPC needed. This avoids importing the bridge dependency chain.

### Environment Variables

The bridge injects routing context into the agent subprocess:

| Variable | Description |
|----------|-------------|
| `VALOR_SESSION_ID` | Bridge session ID for parent lookup |
| `CHAT_ID` | Telegram chat ID for output routing |
| `PROJECT_KEY` | Project key for queue scoping |
| `MESSAGE_ID` | Originating message ID |

The tool reads these to determine where to route self-scheduled session output.

## Deferred Execution (`scheduled_at`)

The `AgentSession` model has a `scheduled_at` field (UTC datetime). When set:

- `_pop_agent_session()` skips sessions where `scheduled_at > now()`
- Sessions with `scheduled_at` in the past are treated as immediate
- Sessions with no `scheduled_at` are always eligible

Usage: `python -m tools.agent_session_scheduler schedule --issue 113 --after "2026-03-12T02:00:00Z"`

## Priority Model

Four-tier priority system replacing the old binary high/low:

| Priority | Rank | Use Case |
|----------|------|----------|
| `urgent` | 0 | Production outage, bumped sessions |
| `high` | 1 | Recovery sessions, interrupted work |
| `normal` | 2 | Default for all new sessions (Telegram messages, scheduled) |
| `low` | 3 | Catchup messages, revival, reflections |

Within the same priority tier, sessions are processed **FIFO** (oldest first), replacing the previous FILO ordering.

## Safety Mechanisms

### Self-Scheduling Depth Cap

Each `AgentSession` tracks `scheduling_depth`. When the tool schedules a session, it increments the parent session's depth. Max depth is 3 -- preventing infinite scheduling loops.

### Rate Limiting

Maximum 30 scheduled sessions per hour per project. Checked before every `schedule` and `push` operation.

### Structured JSON Output

All commands return structured JSON for agent parsing:

```json
{"status": "queued", "agent_session_id": "abc123", "queue_position": 2, "scheduling_depth": 1}
{"status": "error", "message": "Rate limit exceeded"}
```

## Parent-Child Session Hierarchy

Sessions can be decomposed into smaller child sessions linked to a parent via `parent_agent_session_id`. This enables:

- **Partial re-runs**: If child 3/5 fails, only that child needs re-running
- **Progress visibility**: `/queue-status` shows session trees with per-child status
- **Automatic completion**: When all children complete, the parent auto-transitions

### Spawning Child Sessions

An agent mid-session can decompose work by passing `--parent-session`:

```bash
python -m tools.agent_session_scheduler schedule --issue 113 --parent-session $AGENT_SESSION_ID
```

The `AGENT_SESSION_ID` environment variable is injected into the agent subprocess automatically.

Child sessions inherit from the parent:
- `correlation_id` (end-to-end tracing)
- `chat_id` (output routing)
- `classification_type` (SDLC/Q&A classification)
- `working_dir` (project working directory)
- `priority` (unless explicitly overridden)

### Parent Lifecycle

1. Parent spawns children via `schedule --parent-session $AGENT_SESSION_ID`
2. Parent transitions to `waiting_for_children` status
3. Worker processes children sequentially (same as any other sessions)
4. After each child completes, worker checks if all siblings are terminal
5. When all children are done: parent auto-transitions to `completed` or `failed`

### Listing Children

```bash
python -m tools.agent_session_scheduler children --agent-session-id <PARENT_ID>
```

Returns structured JSON with progress summary and per-child status.

### Health Monitoring

The agent session health monitor (runs every 5 minutes) includes hierarchy checks:

- **Orphaned children**: Parent deleted but children still reference it -- clears the link
- **Stuck parents**: `waiting_for_children` with all children terminal -- auto-finalizes

### AgentSession Helpers

```python
session.get_parent()              # Returns parent AgentSession or None
session.get_children()            # Returns list of child AgentSessions
session.get_completion_progress() # Returns (completed, total, failed) tuple
```

## Batch Dispatch

"Handle issues #111, #112, #113" is just the agent calling `schedule` three times:

```bash
python -m tools.agent_session_scheduler schedule --issue 111
python -m tools.agent_session_scheduler schedule --issue 112
python -m tools.agent_session_scheduler schedule --issue 113
```

No special batch API needed.

## Related

- [Agent Session Queue](agent-session-queue.md) -- Core queue infrastructure
- [Agent Session Model](agent-session-model.md) -- AgentSession model fields and lifecycle
- [Chat Dev Session Architecture](chat-dev-session-architecture.md) -- ChatSession orchestrates SDLC pipeline for scheduled sessions
- `/queue-status` skill -- Telegram-accessible queue management
