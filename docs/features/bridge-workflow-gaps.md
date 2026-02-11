# Bridge Workflow Gaps

Three features that close workflow gaps in the Telegram bridge: **output classification**, **auto-continue for status updates**, and **session log snapshots**. Together they reduce unnecessary Telegram noise, keep agents working autonomously, and preserve full session history for debugging.

## Problem

Before these changes, every piece of agent output was sent to Telegram regardless of whether it was a question needing human input or a routine status update. This created two problems:

1. **Noisy chat** -- Status updates like "running tests..." cluttered the Telegram group and demanded attention when none was needed.
2. **Lost context** -- When sessions crashed or were abandoned, there was no persistent record of what the agent had been doing. Debugging required reconstructing state from scattered log files.

## Output Classification

The `classify_output()` function in `bridge/summarizer.py` categorizes every piece of agent output into one of five types:

| OutputType | Meaning | Bridge Behavior |
|---|---|---|
| `QUESTION` | Agent needs human input | Send to Telegram, pause |
| `STATUS_UPDATE` | Progress report, no input needed | Auto-continue (suppress) |
| `COMPLETION` | Work is finished | Send to Telegram |
| `BLOCKER` | Agent is stuck and cannot proceed | Send to Telegram, pause |
| `ERROR` | Something broke | Send to Telegram |

### Classification Strategy

Classification uses a two-tier approach:

1. **LLM classification** (primary) -- Haiku classifies the output and returns a structured JSON response with type, confidence, and reason.
2. **Heuristic fallback** -- If the LLM call fails or returns low confidence (below 0.80 threshold), a regex-based heuristic examines the text for question patterns, error keywords, completion phrases, and other signals.

When confidence falls below `CLASSIFICATION_CONFIDENCE_THRESHOLD` (0.80), the system conservatively defaults to `QUESTION` to pause for human review rather than auto-continuing incorrectly.

## Auto-Continue

When output is classified as `STATUS_UPDATE`, the bridge suppresses it from Telegram and injects `"continue"` into the agent's steering queue. This keeps the agent working without human intervention.

### How It Works

1. Agent produces output (e.g., "Running test suite, 4 of 12 passing so far...")
2. `classify_output()` returns `STATUS_UPDATE`
3. Bridge increments the per-session auto-continue counter
4. If counter is at or below `MAX_AUTO_CONTINUES` (3), the bridge injects `"continue"` via the steering queue
5. If counter exceeds the limit, the output is sent to Telegram as normal (safety valve)

### Safety Limits

- **MAX_AUTO_CONTINUES = 3** -- Prevents infinite loops where the agent generates only status updates. After 3 auto-continues, the next status update goes to Telegram so the human can see what is happening.
- **Counter resets on human reply** -- When the human sends a new message to the session, the auto-continue counter resets to zero.
- **Steering queue integration** -- Auto-continue uses the same steering queue mechanism as manual human input, so the agent sees it as a normal continuation signal.

## Session Log Snapshots

The `bridge/session_logs.py` module saves structured JSON snapshots of session state at key lifecycle events. These snapshots provide a full audit trail for debugging and session recovery.

### Directory Structure

```
logs/sessions/
  {session_id}/
    {timestamp}_resume.json
    {timestamp}_auto_continue.json
    {timestamp}_error.json
    {timestamp}_complete.json
```

### Event Types

| Event | When Saved | What Is Captured |
|---|---|---|
| `resume` | Job starts or resumes | Session ID, job ID, sender, message preview |
| `auto_continue` | Status update triggers auto-continue | Classification result, continue count, message preview |
| `error` | Agent encounters an error | Error details, job ID |
| `complete` | Job finishes successfully | Job ID, sender |

### Cleanup

`cleanup_old_snapshots(max_age_hours=168)` removes session log directories older than 7 days.

## Completion Signal

The thumbs-up emoji reaction (👍) in Telegram serves as a **human-to-human** completion signal meaning "this work is done."

**Telethon cannot receive emoji reaction events** for user accounts -- this is a Telegram API limitation. Therefore:

- The 👍 reaction is purely a visual signal between humans in the group chat
- `mark_work_done()` is called **automatically** at job completion in `agent/job_queue.py`
- No reaction handler exists or is needed in the bridge

## Relevant Files

| File | Purpose |
|---|---|
| `bridge/summarizer.py` | `OutputType` enum, `classify_output()`, heuristic fallback |
| `bridge/session_logs.py` | `save_session_snapshot()`, `cleanup_old_snapshots()` |
| `agent/job_queue.py` | Auto-continue logic in `send_to_chat`, session snapshot integration, `mark_work_done()` |
| `agent/steering.py` | Steering queue used by auto-continue |
| `CLAUDE.md` | Auto-continue rules documentation |

## See Also

- [Session Isolation](session-isolation.md) -- Task list and worktree isolation per session
- [Bridge Self-Healing](bridge-self-healing.md) -- Crash recovery and watchdog system
- [Steering Queue](steering-queue.md) -- Mid-execution course correction mechanism
