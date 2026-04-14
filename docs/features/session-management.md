# Session Management: Reply-Chain Root Resolution

## Overview

When a user replies to any message in a Telegram conversation thread — including Valor's own responses — the bridge resolves the **canonical session_id** by walking the reply chain to find the original human message that started the thread.

This ensures that all replies in a conversation map to **one AgentSession** regardless of which message in the thread is being replied to.

## Problem

Prior to this fix, session_id derivation used `reply_to_msg_id` directly:

```python
# Before (broken for replies to Valor's responses):
session_id = f"tg_{project_key}_{chat_id}_{message.reply_to_msg_id}"
```

This worked for the first reply (directly to the original human message), but broke when the user replied to Valor's response instead:

| Step | Message | reply_to | session_id derived |
|------|---------|----------|-------------------|
| 1 | User msg_8111 | — | `tg_..._8111` (correct) |
| 2 | Valor msg_8113 | — | (outbound) |
| 3 | User msg_8114 | reply_to=8113 | `tg_..._8113` (wrong!) |

Step 3 created a new AgentSession instead of resuming the original, producing duplicate dashboard rows.

## Solution

`resolve_root_session_id()` in `bridge/context.py` walks the reply chain backward to find the oldest non-Valor message, then derives the session_id from that message's ID.

```python
# After (correct):
session_id = await resolve_root_session_id(
    client, event.chat_id, message.reply_to_msg_id, project_key
)
```

## Resolution Strategy

The resolver uses a three-step fallback chain:

1. **Cache-first walk** (`_cache_walk_root`): Query `TelegramMessage.query.filter(chat_id=X, message_id=Y)` to walk the chain using Redis records. No Telegram API calls needed when records are cached. `message_id` is a `KeyField` enabling O(1) indexed lookup.

2. **API fallback** (`fetch_reply_chain`): If any cache lookup misses (record not in Redis), fall back to the Telegram API chain walk (up to 20 hops). Walks the full thread and finds the oldest non-Valor message.

3. **Final fallback**: On any exception (network error, Redis unavailable), return a session_id derived directly from `reply_to_msg_id`. This preserves the old behavior as a safe degraded path — no message delivery is blocked.

## Outbound message_id Storage

As a prerequisite, `send_response_with_files` was updated to return `Message | None` instead of `bool`. The `_send` callback in `bridge/telegram_bridge.py` now captures the returned Telegram `Message.id` and passes it to `store_message`:

```python
sent_msg_id = getattr(sent, "id", None)
store_message(..., message_id=sent_msg_id)
```

This populates `TelegramMessage.message_id` for outbound records, making future cache-first walks effective even for chains that start with a Valor response.

## Data Model Change

`TelegramMessage.message_id` was promoted from a plain `Field` to a `KeyField`:

```python
# Before:
message_id = Field(type=int, null=True)

# After:
message_id = KeyField(null=True)
```

`KeyField` adds a set-based index in Redis that enables `filter(message_id=X)` as an O(1) lookup. No data migration is required — `KeyField` is a superset of `Field` for nullable values.

## Graceful Degradation

The resolver never blocks message delivery:

- Cache miss → API walk (transparent to user)
- API failure → `reply_to_msg_id` direct (old behavior, one extra AgentSession at worst)
- Any exception → `reply_to_msg_id` direct (safe fallback logged at DEBUG level)

## Deterministic Root Caching

Prior to this fix, the three-step fallback chain had a race condition: if Valor's outbound
`TelegramMessage` records weren't in Redis yet when a reply arrived, the cache walk and API
walk could resolve *different* root messages for the same thread, producing non-deterministic
`session_id` values and split sessions.

### Redis Key Scheme

Every successful root resolution (cache walk or API walk) now persists the result to a stable
Redis key before returning:

```
session_root:{chat_id}:{start_msg_id}
```

- **TTL**: 7 days — reply chains don't change, so the key is safe to cache long-term
- **Write semantics**: `SET NX EX 604800` — first writer wins; concurrent resolutions for the
  same thread are de-duped at the Redis layer. This eliminates the race condition.
- **Warm-path behavior**: `_get_cached_root` is checked as Step 0, before `_cache_walk_root`
  and before the Telegram API. A warm key short-circuits all other resolution logic with a
  single O(1) Redis GET — the warm path is *faster* than before.

### Implementation

Two helpers in `bridge/context.py`:

```python
async def _get_cached_root(chat_id: int, msg_id: int) -> int | None:
    """Return the cached root message ID for this chain, or None on miss/error."""
    ...

async def _set_cached_root(chat_id: int, msg_id: int, root_id: int) -> None:
    """Persist the resolved root. SET NX — first writer wins. Fails silently on error."""
    ...
```

Both helpers fail silently — a Redis outage degrades to the original three-step fallback, never
blocks message delivery.

## Completed-Session Resume

When a reply-to message resolves to a `completed` session, the steering check previously fell
through and created a blank-slate re-enqueue with no prior context. The new behavior:

1. The steering check at `bridge/telegram_bridge.py` inspects `completed` sessions after
   the `running`/`active`/`pending` checks find nothing.
2. If a completed session is found, the handler builds a **layered preamble** that carries
   both the prior session's `context_summary` and the live Telegram reply thread. The order
   is fixed so the agent always sees the same shape:
   ```
   [Prior session context: {summary}]

   REPLY THREAD CONTEXT (oldest to newest):
   ----------------------------------------
   Tom: did we get that fixed?
   Valor: yes, shipped yesterday
   ----------------------------------------

   {new message}
   ```
3. The reply-thread block is fetched synchronously via `fetch_reply_chain` +
   `format_reply_chain` with a 3-second timeout. On timeout, network error, or any
   exception the handler logs `RESUME_REPLY_CHAIN_FAIL` and falls back to the
   summary-only preamble — the session always enqueues.
4. A fresh `AgentSession` is enqueued with the **same `session_id`** (preserving thread
   continuity), the augmented message as its task, and `telegram_message_key` set so the
   worker's deferred enrichment still hydrates media, YouTube, and link summaries. The
   deferred enrichment is idempotent: it checks for the canonical `REPLY THREAD CONTEXT`
   header (constant `REPLY_THREAD_CONTEXT_HEADER` in `bridge/context.py`) and skips its
   own reply-chain fetch if the handler already prepended one.
5. No ack is sent. The PM behaves like a human PM resuming a conversation with their CEO:
   they don't announce "picking up where we left off" — they just respond to the substance
   of the new message. The resumed session's actual reply is the only user-visible signal.
6. If `context_summary` is `None` (session completed without generating a summary), a generic
   fallback string is used: `"This continues a previously completed session."` The reply
   thread is still hydrated when available — this is the primary carry for sessions whose
   summary was never written.

This eliminates the "context orphan" scenario where the agent starts from scratch on a task
that was already partially completed.

### Implicit-Context Directive

Messages that reference prior conversation without using Telegram's native reply-to feature
("did we get that fixed?", "the bug is still broken") are detected by the heuristic
`references_prior_context(text)` in `bridge/context.py`. When the predicate matches and the
message has no `reply_to_msg_id`, the handler prepends a `[CONTEXT DIRECTIVE]` block to the
prompt. The directive is advisory tool-order guidance — it instructs the agent to consult
`valor-telegram`, `memory_search`, the project knowledge base, and `gh issue/pr` in that
order, and to skip the directive entirely if the auto-recalled subconscious memory already
covers the reference.

The heuristic is narrow and high-precision (deictic patterns like `the bug`, `that issue`,
`still broken`, `we fixed`, `last time`, `as I mentioned`, `did we`, `what about that`,
combined with the existing `STATUS_QUESTION_PATTERNS`). False positives cost one agent turn
at most. Set the env var `REPLY_CONTEXT_DIRECTIVE_DISABLED=1` to turn the directive off
without a code deploy. Every injection emits a structured log entry
(`implicit_context_directive_injected`) with `session_id`, `chat_id`, `matched_patterns`,
and a text preview so false-positive rates can be audited from logs.

See [Reply-Thread Context Hydration](reply-thread-context-hydration.md) for the full design.

## Race Conditions

Two rapid replies both derive the same root session_id before either is processed. This is handled by `enqueue_agent_session()`'s same-session_id supersede logic (lines 318–336 of `telegram_bridge.py`). No shared mutable state in the chain walk.

## Timestamp Display Format

All timestamp displays in the session CLI include an explicit UTC label to prevent ambiguity when comparing timestamps across sources.

**`python -m tools.valor_session status`** output:
```
Created:  2026-04-07 05:49:00 UTC
Started:  2026-04-07 06:04:28 UTC
Updated:  2026-04-07 06:34:12 UTC
```

**`logs/worker.log`** entries:
```
2026-04-07 13:03:54 UTC worker INFO ...
```

Both surfaces use UTC. The `_format_ts()` helper in `tools/valor_session.py` appends ` UTC` to all formatted timestamps and treats naive ISO strings as UTC (matching internal storage per PR #557). The worker logger uses a `_UTCFormatter` subclass with `converter = time.gmtime` so log lines never show local time.

This avoids the 7-hour offset error that occurs when mixing `valor_session status` (which stores UTC) with worker.log lines that previously used local time (UTC+7).

## Related Features

- [Mid-Session Steering](mid-session-steering.md) — Steering check that routes replies to running sessions uses the same canonical session_id.
- [Semantic Session Routing](semantic-session-routing.md) — Handles unthreaded messages (no reply-to); this feature handles explicit reply-to chains.
- [Session Isolation](session-isolation.md) — Two-tier task list scoping that depends on consistent session_id derivation.
- [Agent Session Model](agent-session-model.md) — AgentSession lifecycle model tracking per session_id.
