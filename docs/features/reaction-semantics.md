# Reaction Semantics

Emoji reaction protocol for message delivery feedback in the Telegram bridge.

## Overview

The bridge uses Telegram emoji reactions as a signaling protocol for message lifecycle phases. The protocol distinguishes between "acknowledged without reply" and "completed with reply delivered" to prevent silent message loss.

## Reaction Constants

All constants are canonically defined in `agent/constants.py` (re-exported from `bridge/response.py` for backward compatibility).

| Constant | Emoji | Meaning |
|----------|-------|---------|
| `REACTION_RECEIVED` | eyes | Message received, queued for processing |
| `REACTION_PROCESSING` | thinking | Agent is actively working |
| `REACTION_SUCCESS` | thumbs up | Acknowledged, no text reply coming |
| `REACTION_COMPLETE` | trophy | Work done, text reply attached and delivered |
| `REACTION_ERROR` | thinking (🤔, pinned) | Error occurred during processing |

## Key Design Decisions

### Receipt-Time Reactions Reflect Agent Action Intent

When a Telegram message arrives, the bridge sets an initial 👀 reaction (eyes), then updates it to an action-intent emoji after classification. The intent is a first-person statement from the bot: "here is what I am about to do."

The emoji is chosen from `ACTION_EMOJI_MAP` in `tools/emoji_embedding.py`, keyed by `work_type` (bug/feature/chore/sdlc). A bug report gets 👨‍💻 or 👀 (investigating). A task gets 🫡 or 👍 (will do). Unclassified messages fall back to 👀 (general). This replaces the previous content-sentiment approach that mirrored the user's mood back at them and was vulnerable to offensive emoji matches.

### Success vs. Complete

The distinction between `REACTION_SUCCESS` and `REACTION_COMPLETE` is critical:

- **REACTION_SUCCESS** means "the agent processed your message and has nothing to say back." This covers status updates suppressed by auto-continue and cases where the agent's work product is an action rather than text.
- **REACTION_COMPLETE** means "the agent finished AND a text reply was delivered to you." This uses `messenger.has_communicated()` to verify that at least one message was actually sent before claiming completion.

Without this distinction, a failure to deliver a reply could be masked by a thumbs-up reaction, making the user believe everything succeeded.

### REACTION_ERROR Is Pinned, Not Semantic

`REACTION_SUCCESS` and `REACTION_COMPLETE` resolve semantically: `find_best_emoji()` embeds a feeling string and finds the nearest emoji by cosine similarity, so the exact result can vary as the embedding index changes. `REACTION_ERROR` does not follow that path. It is pinned to a fixed emoji, 🤔 (U+1F914), via a `_TerminalEmojiConfig` NamedTuple in `agent/constants.py` with `pinned=True`. Resolution short-circuits straight to the fixed emoji: no `find_best_emoji()` call, no embedding lookup, no dependence on `OPENROUTER_API_KEY` or the on-disk embeddings cache.

This is deliberate. An error reaction lands on the user's own message. A semantic draw over an "error / something went wrong" feeling string can surface faces that read as hostile toward the person who sent that message (for example, a scream face lands closer to blame than to distress). Pinning `REACTION_ERROR` to 🤔 removes that lottery entirely: every error, in every environment, produces the same deterministic, non-hostile reaction. See [Emoji Embedding Reactions](emoji-embedding-reactions.md#terminal-reactions) for the resolution table covering all three terminal constants.

### Invalid Reactions

Telegram only accepts a specific subset of emoji as reactions. Common emoji that are explicitly banned in `INVALID_REACTIONS`:

- Cross mark: `ReactionInvalidError` -- not in Telegram's allowed set
- Check mark: Not a valid Telegram reaction
- Hourglass: Not a valid Telegram reaction
- Arrows: Not a valid Telegram reaction

The full list of validated working reactions is maintained in `VALIDATED_REACTIONS` in `bridge/response.py`.

### Blocked Reactions (policy)

Some emoji are *valid* Telegram reactions but are deliberately excluded from selection on policy grounds. Every reaction this system sets lands on a user's own message, so an outward-directed hostile face reads as blame at the person who messaged us. `BLOCKED_REACTION_EMOJIS` in `tools/emoji_embedding.py` is the single source of truth for this policy: it names every emoji that must never target a user, and `find_best_emoji()` filters them out of candidate scoring at selection time, before scoring picks a winner.

The middle finger 🖕 was the original case: it is removed entirely from both `VALIDATED_REACTIONS` and the `EMOJI_LABELS` selection set, and `BLOCKED_REACTION_EMOJIS` guards it defensively against stale on-disk embedding caches (`data/emoji_embeddings.json`) that may still contain its embedding. Five hostile faces joined the blocklist afterward: thumbs down 👎, face with symbols on mouth 🤬, pouting face 😡, face vomiting 🤮, and face screaming in fear 😱. Unlike the middle finger, these five stay in `VALIDATED_REACTIONS` (Telegram accepts them as reactions) and are excluded solely by `BLOCKED_REACTION_EMOJIS` at selection time. Self-directed sadness and worry (😢 😭 😨) stays selectable: it expresses empathy toward the user, not hostility. See [Emoji Embedding Reactions](emoji-embedding-reactions.md#components) for the module-level detail.

## Auto-Continue Integration

Reactions interact with the auto-continue system. When auto-continue is active, reaction updates are deferred until the final session completes.

### Flow

1. Agent completes a turn. The output router (`agent/output_router.py`, called from `agent/agent_session_queue.py`) decides whether to **nudge** (auto-continue) or **deliver** (send to Telegram).
2. If Observer decides **STEER**: suppress the output, re-enqueue with nudge feedback, defer reaction.
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

The fix re-enqueues a new session through the normal session queue. This guarantees the message is processed because it follows the same path as any incoming Telegram message, with full session context (session_id, slug, task_list_id) preserved.

## Silent Loss Prevention

Three paths to silent text loss have been identified and guarded:

### 1. Auto-Continue Steering Race

**Problem:** Steering queue injection could race with agent exit, dropping the "continue" message silently.

**Fix:** Replace steering queue injection with session re-enqueue through the normal session queue.

### 2. Tool Log Filtering

**Problem:** `filter_tool_logs()` strips tool-use prefix lines from agent output. If it strips everything from a non-empty response, the user receives nothing.

**Fix:** If `filter_tool_logs()` reduces a non-empty string to empty, fall back to "Done." so the user always gets a response.

### 3. Unconditional Success Reaction

**Problem:** Setting `REACTION_SUCCESS` unconditionally after processing could mask a failure to deliver the actual reply text.

**Fix:** Use `messenger.has_communicated()` to check whether a text message was actually sent. Set `REACTION_COMPLETE` only if verified; otherwise fall back to `REACTION_SUCCESS` (indicating ack without reply) or `REACTION_ERROR` on errors.

## Relevant Files

| File | Role |
|------|------|
| `agent/constants.py` | Canonical location for `REACTION_SUCCESS/COMPLETE/ERROR` constants |
| `bridge/response.py` | Re-exports reaction constants, `filter_tool_logs`, `set_reaction`, `VALIDATED_REACTIONS` (`OutputType` enum was removed in drafter_passthrough_validation) |
| `agent/agent_session_queue.py` | Reaction selection logic, auto-continue re-enqueue, has_communicated() check |
| `agent/messenger.py` | BossMessenger with `has_communicated()` tracking, BackgroundTask with internal health watchdog |
| `agent/agent_session_queue.py` | Nudge loop: output routing decisions via `determine_delivery_action()` |
| `tests/test_reply_delivery.py` | Tests for steering drain, reaction selection, filter fallback |
| `tests/unit/test_reaction_never_hostile.py` | Locks `REACTION_ERROR`'s pin to 🤔 and the exact `BLOCKED_REACTION_EMOJIS` hostile deny-list |

## See Also

- [Bridge Workflow Gaps](bridge-workflow-gaps.md) -- Output classification and auto-continue behavior
- [Steering Queue: Historical Spec](steering-implementation-spec.md) -- The steering mechanism (now only used for live human corrections, not auto-continue)
- [Session Isolation](session-isolation.md) -- How session context is preserved across re-enqueued jobs
