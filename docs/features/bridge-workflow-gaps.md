# Bridge Workflow Gaps

Three features that close workflow gaps in the Telegram bridge: **output classification**, **auto-continue for status updates**, and **session log snapshots**. Together they reduce unnecessary Telegram noise, keep agents working autonomously, and preserve full session history for debugging.

## Problem

Before these changes, every piece of agent output was sent to Telegram regardless of whether it was a question needing human input or a routine status update. This created two problems:

1. **Noisy chat** -- Status updates like "running tests..." cluttered the Telegram group and demanded attention when none was needed.
2. **Lost context** -- When sessions crashed or were abandoned, there was no persistent record of what the agent had been doing. Debugging required reconstructing state from scattered log files.

## Output Classification

> **Updated**: Output classification is now handled by the [Observer Agent](observer-agent.md) (PR #321). The `classify_output()` function and its five-type `OutputType` enum have been removed from the routing path. The Observer makes unified STEER/DELIVER decisions with full session context instead of isolated output classification. This section is retained for historical reference.

The old system categorized output into five types (`QUESTION`, `STATUS_UPDATE`, `COMPLETION`, `BLOCKER`, `ERROR`) using a two-tier approach: Haiku LLM classification with a regex heuristic fallback. This was replaced because the classifier had no session context — it couldn't know which pipeline stage was active or whether stages remained.

## Auto-Continue

The bridge uses a two-path auto-continue strategy based on whether the job is an SDLC pipeline job or a casual/ad-hoc job.

### Observer-Driven Routing (Current)

All routing decisions — SDLC and non-SDLC — are now made by the [Observer Agent](observer-agent.md):

1. Worker agent produces output
2. **Stage Detector** (deterministic, pure function) parses transcript for `/do-*` skill invocations and completion markers, updates `AgentSession` stages
3. **Observer Agent** (Sonnet) reads full session state and makes a unified decision:
   - **STEER**: Re-enqueue with a coaching message directing the worker to the next stage
   - **DELIVER**: Send output to Telegram for human review
4. **Hard guard** in `job_queue.py`: auto-continue cap enforced regardless of Observer decision

### Decision Matrix

| Session state | Worker output | Observer decision |
|---|---|---|
| Stages remaining | Status update (no question) | STEER with coaching |
| Stages remaining | Genuine question for human | DELIVER |
| Stages remaining | Error/blocker | DELIVER |
| All stages done | Completion with evidence | DELIVER |
| Non-SDLC job | Any output | DELIVER (Observer recognizes non-pipeline context) |
| Cap reached (10 SDLC / 3 non-SDLC) | Any | DELIVER (hard guard) |

### Safety Limits

- **Error bypass (crash guard)** -- If output is classified as `ERROR`, auto-continue is skipped entirely and the error is sent straight to Telegram. This prevents cascading retry loops when the SDK crashes.
- **MAX_AUTO_CONTINUES = 3** -- Safety cap for non-SDLC jobs. After 3 auto-continues, the next status update goes to Telegram.
- **MAX_AUTO_CONTINUES_SDLC = 10** -- Higher safety cap for SDLC jobs where stage progress is the primary termination signal.
- **Counter resets on human reply** -- When the human sends a new message to the session, the auto-continue counter resets to zero.
- **Steering queue integration** -- Auto-continue uses the same steering queue mechanism as manual human input, so the agent sees it as a normal continuation signal.
- **Merge gate** -- After REVIEW + DOCS stages complete, the SDLC pipeline checks auto-merge eligibility (no open questions, clean review with 0 issues, all tests pass, diff < 150 lines). Eligible PRs are auto-merged; all others stop and wait for explicit human instruction. Tech debt and nits from reviews are patched before docs (not skipped).

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
| `bridge/observer.py` | Observer Agent: unified routing decisions with session context |
| `bridge/stage_detector.py` | Deterministic stage detection (pure function) |
| `bridge/session_logs.py` | `save_session_snapshot()`, `cleanup_old_snapshots()` |
| `agent/job_queue.py` | Observer wiring in `send_to_chat`, hard cap enforcement, `mark_work_done()` |
| `models/agent_session.py` | `is_sdlc_job()`, `has_remaining_stages()`, `has_failed_stage()`, `queued_steering_messages` |
| `CLAUDE.md` | Auto-continue rules documentation |

## See Also

- [Session Isolation](session-isolation.md) -- Task list and worktree isolation per session
- [Bridge Self-Healing](bridge-self-healing.md) -- Crash recovery and watchdog system
- [Steering Queue](steering-queue.md) -- Mid-execution course correction mechanism
