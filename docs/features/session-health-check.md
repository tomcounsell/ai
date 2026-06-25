# Session Health Check (Watchdog Hook)

PostToolUse hook that monitors agent sessions for stuck loops using two complementary checks: a cheap per-call consecutive-failure circuit breaker, and a Haiku judge that runs periodically.

## Overview

The watchdog hook (`agent/health_check.py`) fires after every tool call in a Claude Code subprocess. It runs two layers:

- **Every tool call** — a deterministic consecutive-failure circuit breaker (see below) counts back-to-back failed tool calls.
- **Every `CHECK_INTERVAL` (20) tool calls** — it reads the recent activity transcript and asks Haiku whether the agent is making meaningful progress or is stuck in a repetitive loop.

If judged unhealthy (by either layer), the watchdog:
1. Sets `watchdog_unhealthy` on the AgentSession model (so the nudge loop delivers output instead of auto-continuing)
2. Injects a STOP directive via `additionalContext` (Haiku layer only — so Claude sees the alert immediately)

## Consecutive-Failure Circuit Breaker (issue #1413)

Wired inside `watchdog_hook` immediately after the per-session tool counter increments, this is a cheap deterministic complement to the Haiku judge. It counts consecutive failed tool calls per session and trips once `CONSECUTIVE_FAILURE_THRESHOLD` (default 5) failures occur in a row.

- **Failure classification** (`_is_tool_failure()`): a `tool_response` dict with `is_error == True`, or — on rare/edge SDK paths — a string starting with `"Error: "`. Everything else (`None`, lists, scalars, dicts without `is_error`) is treated as success, biased toward avoiding false positives. Bash exit codes are not inspected; the SDK marks failed Bash via `is_error`.
- **Reset on success**: any successful tool call zeroes the counter and clears the recent-failure ring.
- **Reason string**: when tripped, `_set_unhealthy()` is called with a reason naming the last failing tools, e.g. `"5 consecutive tool failures (Bash, Bash, Edit, Read, Bash) — strategy reassessment required"`. The recent tool names come from a `deque(maxlen=5)` ring. The distinctive `"N consecutive tool failures"` prefix lets dashboards attribute which writer set the shared `watchdog_unhealthy` field.
- **Re-fire**: after tripping, the counter and ring reset, so the breaker re-fires every 5 *additional* consecutive failures.
- **State**: `_consecutive_failures` and `_recent_failures` are process-local in-memory dicts (same lifecycle as `_tool_counts` — reset on worker restart). No `AgentSession` schema change, no Redis persistence.

The breaker and the Haiku judge write the same single, latching `watchdog_unhealthy` field (last-writer-wins; `clear_unhealthy` is not called in production), so a re-fire refreshes the reason text rather than producing additional nudge-loop pauses.

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
- **Skip-tool** (issue #1711): Prepended by `_apply_recovery_transition` when the per-tool timeout sub-loop detects a wedged tool and requeues the session. The message names the timed-out tool, embeds the original request verbatim, and instructs the model to answer without the hung tool. It is prepended at index 0 (`push_steering_message(..., front=True)`) so the model receives the skip instruction before any previously-queued steering entries. See [Session Steering §Automatic Steering on tool_timeout Recovery](session-steering.md#automatic-steering-on-tool_timeout-recovery).

## Key Functions

| Function | Purpose |
|----------|---------|
| `watchdog_hook()` | Main PostToolUse hook entry point |
| `_check_tool_failure_breaker()` | Update consecutive-failure counter + ring; trip breaker at threshold |
| `_is_tool_failure()` | Classify a `tool_response` as failure (dict `is_error` / `"Error: "` string) |
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
| `CHECK_INTERVAL` | 20 | Haiku health check fires every N tool calls |
| `CONSECUTIVE_FAILURE_THRESHOLD` | 5 | Consecutive failed tool calls that trip the circuit breaker |

## Related

- [agent-session-health-monitor.md](agent-session-health-monitor.md) -- Queue-level health monitoring (complementary layer). See its **Per-Tool Timeout Sub-Loop** section for the parallel 30s detector that fires when a tool's PreToolUse hook fires but PostToolUse never returns (issue #1270). The same monitor's `_has_progress` Tier 1 sub-check B is bounded by a no-output running-time budget (issue #1356) so sessions whose SDK never emits a first turn cannot hold Tier 1 open indefinitely on a fresh asyncio-task heartbeat alone.
- [bridge-self-healing.md](bridge-self-healing.md) -- Bridge process-level crash recovery
- `agent/health_check.py` -- Implementation source
- `tests/unit/test_health_check.py` -- Unit tests (Haiku judge + activity stream)
- `tests/unit/test_tool_failure_breaker.py` -- Unit tests (consecutive-failure circuit breaker)
- Issue #1413 -- Tool-failure circuit breaker
- Issue #625 -- Context enrichment tracking issue
- Issue #374 -- Prior fix for stale session counts
- Issue #1270 -- Per-tool timeout enforcement with per-tier counters (sub-loop in `agent/session_health.py`, parallel to the watchdog hook here)
