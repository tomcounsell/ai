# Reply-Thread Context Hydration

**Status:** Shipped
**Tracking:** [#949](https://github.com/tomcounsell/ai/issues/949)
**Plan:** [`docs/plans/reply_thread_context_hydration.md`](../plans/reply_thread_context_hydration.md)

## Problem

Telegram replies — either via the native Reply feature or by referencing
prior state ("did we get that fixed?", "the bug is still broken") —
sometimes reached the agent stripped of their context. Three paths lost
information silently:

1. **Resume-completed branch.** When a reply resolved to a `completed`
   session, `_build_completed_resume_text` injected only `context_summary`.
   The reply-chain messages were never fetched, and `telegram_message_key`
   was omitted from the re-enqueue so the worker's deferred enrichment
   (media, YouTube, links, reply chain) silently skipped.
2. **Deferred enrichment pre-condition.** `enrich_message` keys off
   `TelegramMessage.query.filter(msg_id=session.telegram_message_key)`.
   Any enqueue path that forgot to set the key dropped every enrichment
   step with a DEBUG log.
3. **Implicit context.** Messages without `reply_to_msg_id` that clearly
   referenced prior state ("we fixed", "last time", "as I mentioned") got
   no special treatment — the `build_conversation_history` docstring said
   the agent *should* reach for `valor-telegram`, but nothing in the
   prompt prompted it to.

Result: the agent asked for clarification on a topic that was literally
ten messages up in the same chat.

## Solution Overview

Three coordinated changes across `bridge/context.py`,
`bridge/telegram_bridge.py`, and `agent/agent_session_queue.py`:

| Change | Scope | Effect |
|--------|-------|--------|
| **A** | Resume-completed branch | Always fetches the reply thread and appends it to the summary preamble. Short 3-second sync timeout with clean fallback. |
| **B** | Layered preamble | `_build_completed_resume_text` accepts an optional `reply_chain_context` parameter and places it between the summary and the follow-up text. Empty/None is a no-op so legacy callers are unchanged. |
| **C** | Implicit-context directive | `references_prior_context(text)` predicate + a `[CONTEXT DIRECTIVE]` block prepended when the predicate matches and the message has no `reply_to_msg_id`. |

## The Canonical Header

`bridge/context.py` exports a single constant:

```python
REPLY_THREAD_CONTEXT_HEADER = "REPLY THREAD CONTEXT"
```

This string is the canonical substring used by:

- `format_reply_chain` when rendering a chain block (always produces exactly one header).
- `agent/agent_session_queue.py` deferred enrichment for its idempotency check.
- Every test that asserts "exactly one block per prompt".

Do not duplicate this string. Import from `bridge.context`.

## Flow

### Reply-To Arrives, Resolves To Completed Session

```
Telegram reply (reply_to_msg_id=42, clean_text="did we fix this?")
        │
        ▼
bridge/telegram_bridge.py  handler
  (resume-completed branch)
        │
        ├── is_duplicate_message(chat_id, msg_id)?  -> return early (IN-4)
        │
        ├── fetch_reply_chain(client, chat_id, reply_to_msg_id)
        │     with asyncio.wait_for(..., timeout=3.0)
        │     on Timeout/Exception:  logger.warning("RESUME_REPLY_CHAIN_FAIL ...")
        │                            reply_chain_context = None
        │     on success:            reply_chain_context = format_reply_chain(chain)
        │
        ├── augmented_text = _build_completed_resume_text(
        │       completed, clean_text,
        │       reply_chain_context=reply_chain_context)
        │
        ├── enqueue_agent_session(
        │       message_text=augmented_text,
        │       telegram_message_key=stored_msg_id,   # <-- was missing
        │       ...)
        │
        └── record_message_processed(chat_id, msg_id)
```

The worker later runs deferred enrichment. If the canonical header is
already present in `session.message_text` the reply-chain fetch is
skipped — media, YouTube, and link summaries still run normally. This is
the idempotency guard for Race 1.

### Non-Reply Message With Implicit Context

```
Telegram message (reply_to_msg_id=None, clean_text="did we ship the bug fix?")
        │
        ▼
bridge/telegram_bridge.py  handler
  (normal enqueue path)
        │
        ├── os.getenv("REPLY_CONTEXT_DIRECTIVE_DISABLED") ?  -> skip directive
        │
        ├── references_prior_context(clean_text) ?  -> True
        │
        ├── matched = matched_context_patterns(clean_text)
        │
        ├── logger.info("implicit_context_directive_injected",
        │       extra={session_id, chat_id, matched_patterns, text_preview})
        │
        ├── enqueued_message_text = "[CONTEXT DIRECTIVE] ...\n\n" + clean_text
        │
        └── enqueue_agent_session(message_text=enqueued_message_text, ...)
```

The directive is advisory: it tells the agent to reach for
`valor-telegram`, `memory_search`, the project knowledge base, and
`gh issue/pr` *if the auto-recalled subconscious memory does not cover
the reference*. False positives cost at most one tool call.

## Precedence Between Pre-Hydration And Deferred Enrichment

| Path | Who adds the chain block? | Idempotency |
|------|---------------------------|-------------|
| Normal new session (reply-to present) | Worker's deferred enrichment (`enrich_message` step 4) | Handler never pre-hydrates here, so no conflict |
| Resume-completed branch (reply-to present) | Handler pre-hydrates synchronously | Worker checks `REPLY_THREAD_CONTEXT_HEADER in message_text` and skips step 4 |
| Implicit-context (no reply-to) | Neither adds a chain block; only the `[CONTEXT DIRECTIVE]` is prepended | N/A |

The guarantee: **the agent sees exactly one `REPLY THREAD CONTEXT` block
per prompt.** Regression is prevented by
`test_no_double_hydration_when_handler_prehydrates` in
`tests/integration/test_steering.py`.

## Failure Paths

| Failure | Behavior |
|---------|----------|
| `fetch_reply_chain` raises | `logger.warning("RESUME_REPLY_CHAIN_FAIL exception ...")` with `session_id`, `chat_id`, `reply_to_msg_id`, and `error` fields. Session still enqueues with summary-only preamble. |
| `fetch_reply_chain` exceeds 3s | `logger.warning("RESUME_REPLY_CHAIN_FAIL timeout ...")`. Same fallback. |
| `format_reply_chain([])` | Returns `""`. `_build_completed_resume_text` with `reply_chain_context=""` is a no-op -- output identical to summary-only. |
| `references_prior_context(None)` / `""` / `"   "` / non-string | Returns `False` — no directive injection. |
| `REPLY_CONTEXT_DIRECTIVE_DISABLED=1` | Directive injection is skipped regardless of heuristic match (no code deploy required). |

## Key Files

- `bridge/context.py` — `REPLY_THREAD_CONTEXT_HEADER` constant,
  `STATUS_QUESTION_PATTERNS`, `DEICTIC_CONTEXT_PATTERNS`,
  `references_prior_context`, `matched_context_patterns`,
  `fetch_reply_chain`, `format_reply_chain`.
- `bridge/telegram_bridge.py` — `_build_completed_resume_text`
  (accepts `reply_chain_context`), resume-completed handler branch,
  implicit-context directive injection.
- `agent/agent_session_queue.py` — Deferred-enrichment idempotency guard
  (skips reply-chain fetch when the canonical header is present).

## Rollback

All three changes are additive. Rollback path:

1. Set `REPLY_CONTEXT_DIRECTIVE_DISABLED=1` to turn off Change C without a deploy.
2. Revert `_build_completed_resume_text` and its caller in the handler
   to restore summary-only hydration (Changes A + B).
3. The constant, `references_prior_context`, and the idempotency guard
   can stay — they are inert without the handler call sites.

No schema changes, no migrations. A full revert restores pre-PR behavior
bit-for-bit.

## Tests

- `tests/unit/test_context_helpers.py` — 45 tests covering the
  `references_prior_context` contract, deictic/status pattern matches,
  negative guards (None/empty/whitespace/non-string), and
  `_build_completed_resume_text` layering.
- `tests/integration/test_steering.py::TestResolveRootSessionId` —
  5 new tests: `test_reply_to_completed_session_fallback_without_summary`,
  `test_resume_completed_carries_reply_chain`,
  `test_no_double_hydration_when_handler_prehydrates`,
  `test_implicit_context_directive_injected`,
  `test_reply_chain_fetch_failure_falls_back`.

## Related Features

- [Session Management](session-management.md) — Canonical session_id
  derivation; the Completed-Session Resume subsection documents the
  hydration flow end-to-end.
- [Bridge Module Architecture](bridge-module-architecture.md) — Sub-module
  boundaries; `bridge/context.py` owns the heuristic helpers.
- [Subconscious Memory](subconscious-memory.md) — The `[CONTEXT DIRECTIVE]`
  explicitly defers to auto-recalled memory before instructing the agent
  to fetch more context.
- [Agent Session Queue](agent-session-queue.md) — Deferred enrichment
  pipeline (`enrich_message`) that this feature coordinates with.
