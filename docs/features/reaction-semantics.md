# Reaction Semantics

Emoji reaction protocol for message delivery feedback in the Telegram bridge.

## Overview

The bridge uses Telegram emoji reactions as a signaling protocol for message lifecycle phases. The protocol distinguishes between "acknowledged without reply" and "completed with reply delivered" to prevent silent message loss.

## Reaction Constants

All constants are defined in `bridge/response.py`.

| Constant | Emoji | Meaning |
|----------|-------|---------|
| `REACTION_RECEIVED` | eyes | Message received, queued for processing |
| `REACTION_PROCESSING` | thinking | Agent is actively working |
| `REACTION_SUCCESS` | thumbs up | Acknowledged, no text reply coming |
| `REACTION_COMPLETE` | trophy | Work done, text reply attached and delivered |
| `REACTION_ERROR` | scream | Error occurred during processing |

## Key Design Decisions

### Success vs. Complete

The distinction between `REACTION_SUCCESS` and `REACTION_COMPLETE` is critical:

- **REACTION_SUCCESS** means "the agent processed your message and has nothing to say back." This covers status updates suppressed by auto-continue and cases where the agent's work product is an action rather than text.
- **REACTION_COMPLETE** means "the agent finished AND a text reply was delivered to you." This uses `messenger.has_communicated()` to verify that at least one message was actually sent before claiming completion.

Without this distinction, a failure to deliver a reply could be masked by a thumbs-up reaction, making the user believe everything succeeded.

### Invalid Reactions

Telegram only accepts a specific subset of emoji as reactions. Common emoji that are explicitly banned in `INVALID_REACTIONS`:

- Cross mark: `ReactionInvalidError` -- not in Telegram's allowed set
- Check mark: Not a valid Telegram reaction
- Hourglass: Not a valid Telegram reaction
- Arrows: Not a valid Telegram reaction

The full list of 75+ validated working reactions is maintained in `VALIDATED_REACTIONS` in `bridge/response.py`.

## Auto-Continue Integration

Reactions interact with the auto-continue system. When auto-continue is active, reaction updates are deferred until the final job completes.

### Flow

1. Agent completes a turn. The nudge loop in `agent/job_queue.py` decides whether to **nudge** (auto-continue) or **deliver** (send to Telegram).
2. If Observer decides **STEER**: suppress the output, re-enqueue with coaching message, defer reaction.
3. If Observer decides **DELIVER**: send the response and set the appropriate reaction based on content.
4. The auto-continue counter resets when the human sends a new message.

### Routing Decision to Reaction Mapping

| Routing Decision | Content Signal | Reaction |
|------------------|---------------|----------|
| Deliver | Completion with evidence | `REACTION_COMPLETE` (verified via `has_communicated()`) |
| Nudge | Status update, stages remain | Deferred (no emoji until final resolution) |
| Deliver | Question for human | None (awaiting human reply) |
| Deliver | Error/blocker | `REACTION_ERROR` |

### Why Job Re-Enqueue Instead of Steering Queue

The original auto-continue implementation injected a "continue" message into the agent's steering queue. This created a race condition: if the agent had already exited its processing loop, the steering message was silently dropped, and the user received no response at all.

The fix re-enqueues a new job through the normal job queue. This guarantees the message is processed because it follows the same path as any incoming Telegram message, with full session context (session_id, work_item_slug, task_list_id) preserved.

## Silent Loss Prevention

Three paths to silent text loss have been identified and guarded:

### 1. Auto-Continue Steering Race

**Problem:** Steering queue injection could race with agent exit, dropping the "continue" message silently.

**Fix:** Replace steering queue injection with job re-enqueue through the normal job queue.

### 2. Tool Log Filtering

**Problem:** `filter_tool_logs()` strips tool-use prefix lines from agent output. If it strips everything from a non-empty response, the user receives nothing.

**Fix:** If `filter_tool_logs()` reduces a non-empty string to empty, fall back to "Done." so the user always gets a response.

### 3. Unconditional Success Reaction

**Problem:** Setting `REACTION_SUCCESS` unconditionally after processing could mask a failure to deliver the actual reply text.

**Fix:** Use `messenger.has_communicated()` to check whether a text message was actually sent. Set `REACTION_COMPLETE` only if verified; otherwise fall back to `REACTION_SUCCESS` (indicating ack without reply) or `REACTION_ERROR` on errors.

## Relevant Files

| File | Role |
|------|------|
| `bridge/response.py` | Reaction constants, OutputType enum, MAX_AUTO_CONTINUES, filter_tool_logs |
| `agent/job_queue.py` | Reaction selection logic, auto-continue re-enqueue, has_communicated() check |
| `agent/messenger.py` | BossMessenger with `has_communicated()` tracking, BackgroundTask with internal health watchdog |
| `agent/job_queue.py` | Nudge loop: output routing decisions via `classify_nudge_action()` |
| `tests/test_reply_delivery.py` | Tests for steering drain, reaction selection, filter fallback |

## See Also

- [Bridge Workflow Gaps](bridge-workflow-gaps.md) -- Output classification and auto-continue behavior
- [Steering Queue](steering-queue.md) -- The steering mechanism (now only used for live human corrections, not auto-continue)
- [Session Isolation](session-isolation.md) -- How session context is preserved across re-enqueued jobs
