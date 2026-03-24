# Job Self-Scheduling: Agent-Initiated Queue Operations

**Status**: Shipped

## Overview

The agent can programmatically schedule SDLC runs for GitHub issues, enqueue arbitrary Q&A jobs, and manage queue state -- all mid-conversation via the `tools/job_scheduler.py` CLI tool.

## Usage

### Schedule SDLC Work for a GitHub Issue

```bash
python -m tools.job_scheduler schedule --issue 113
python -m tools.job_scheduler schedule --issue 113 --priority high
python -m tools.job_scheduler schedule --issue 113 --after "2026-03-12T02:00:00Z"
```

The tool validates the issue exists via `gh issue view`, constructs an SDLC dispatch message, and creates an `AgentSession` directly in Redis via Popoto ORM.

### Push Arbitrary Message Jobs

```bash
python -m tools.job_scheduler push --message "What is the architecture?" --project valor
python -m tools.job_scheduler push --message "Fix the bug" --priority high
```

### Queue Inspection

```bash
python -m tools.job_scheduler status
python -m tools.job_scheduler status --project valor
```

### Queue Manipulation

```bash
python -m tools.job_scheduler bump --job-id <JOB_ID>    # Move to top (priority=urgent)
python -m tools.job_scheduler pop --project valor         # Remove next without executing
python -m tools.job_scheduler cancel --job-id <JOB_ID>   # Cancel specific job
```

## Architecture

### Tool, Not MCP Server

The agent runs inside Claude Code with Bash access. A Python CLI tool (`tools/job_scheduler.py`) is simpler than an MCP server. The agent calls it via `python -m tools.job_scheduler schedule --issue 113`.

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

The tool reads these to determine where to route self-scheduled job output.

## Deferred Execution (`scheduled_after`)

The `AgentSession` model has a `scheduled_after` field (UTC timestamp). When set:

- `_pop_job()` skips jobs where `scheduled_after > now()`
- Jobs with `scheduled_after` in the past are treated as immediate
- Jobs with no `scheduled_after` are always eligible

Usage: `python -m tools.job_scheduler schedule --issue 113 --after "2026-03-12T02:00:00Z"`

## Priority Model

Four-tier priority system replacing the old binary high/low:

| Priority | Rank | Use Case |
|----------|------|----------|
| `urgent` | 0 | Production outage, bumped jobs |
| `high` | 1 | Recovery jobs, interrupted work |
| `normal` | 2 | Default for all new jobs (Telegram messages, scheduled) |
| `low` | 3 | Catchup messages, revival, reflections |

Within the same priority tier, jobs are processed **FIFO** (oldest first), replacing the previous FILO ordering.

## Safety Mechanisms

### Self-Scheduling Depth Cap

Each `AgentSession` tracks `scheduling_depth`. When the tool schedules a job, it increments the parent session's depth. Max depth is 3 -- preventing infinite scheduling loops.

### Rate Limiting

Maximum 30 scheduled jobs per hour per project. Checked before every `schedule` and `push` operation.

### Structured JSON Output

All commands return structured JSON for agent parsing:

```json
{"status": "queued", "job_id": "abc123", "queue_position": 2, "scheduling_depth": 1}
{"status": "error", "message": "Rate limit exceeded"}
```

## Parent-Child Job Hierarchy

Jobs can be decomposed into smaller child jobs linked to a parent via `parent_job_id`. This enables:

- **Partial re-runs**: If child 3/5 fails, only that child needs re-running
- **Progress visibility**: `/queue-status` shows job trees with per-child status
- **Automatic completion**: When all children complete, the parent auto-transitions

### Spawning Child Jobs

An agent mid-job can decompose work by passing `--parent-job`:

```bash
python -m tools.job_scheduler schedule --issue 113 --parent-job $JOB_ID
```

The `JOB_ID` environment variable is injected into the agent subprocess automatically.

Child jobs inherit from the parent:
- `correlation_id` (end-to-end tracing)
- `chat_id` (output routing)
- `classification_type` (SDLC/Q&A classification)
- `working_dir` (project working directory)
- `priority` (unless explicitly overridden)

### Parent Lifecycle

1. Parent spawns children via `schedule_job --parent-job $JOB_ID`
2. Parent transitions to `waiting_for_children` status
3. Worker processes children sequentially (same as any other jobs)
4. After each child completes, worker checks if all siblings are terminal
5. When all children are done: parent auto-transitions to `completed` or `failed`

### Listing Children

```bash
python -m tools.job_scheduler children --job-id <PARENT_JOB_ID>
```

Returns structured JSON with progress summary and per-child status.

### Health Monitoring

The job health monitor (runs every 5 minutes) includes hierarchy checks:

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
python -m tools.job_scheduler schedule --issue 111
python -m tools.job_scheduler schedule --issue 112
python -m tools.job_scheduler schedule --issue 113
```

No special batch API needed.

## Related

- [Job Queue](job-queue.md) -- Core queue infrastructure
- [Job Dependency Tracking](job-dependency-tracking.md) -- Sibling dependencies, branch mapping, PM queue controls
- [Chat Dev Session Architecture](chat-dev-session-architecture.md) -- ChatSession orchestrates SDLC pipeline for scheduled jobs
- `/queue-status` skill -- Telegram-accessible queue management
