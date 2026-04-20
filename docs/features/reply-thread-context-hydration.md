# Reply-Thread Context Hydration

**Status:** Shipped
**Tracking:** [#949](https://github.com/tomcounsell/ai/issues/949) (original), [#1064](https://github.com/tomcounsell/ai/issues/1064) (fresh-session extension)
**Plans:** [`docs/plans/reply_thread_context_hydration.md`](../plans/reply_thread_context_hydration.md), [`docs/plans/reply_chain_fresh_session.md`](../plans/reply_chain_fresh_session.md)

## Problem

Telegram replies — either via the native Reply feature or by referencing
prior state ("did we get that fixed?", "the bug is still broken") —
sometimes reached the agent stripped of their context. Four paths lost
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
4. **Fresh session + reply to non-Valor message** (issue #1064). Two
   overlapping gating predicates — `is_reply_to_valor` in the bridge
   and `not message.reply_to_msg_id` in the `[CONTEXT DIRECTIVE]`
   block — created an unintended dead zone: a Telegram user replying
   to **another user's** message (not Valor's) that created a fresh
   session got neither the resume-completed pre-hydration (scoped to
   `is_reply_to_valor=True`) nor the implicit-context directive (gated
   off for reply-to messages). The agent received only the reply
   itself, with no trace of the thread it was replying to.

Result: the agent asked for clarification on a topic that was literally
ten messages up in the same chat.

## Solution Overview

Four coordinated changes across `bridge/context.py`,
`bridge/telegram_bridge.py`, and `agent/session_executor.py`:

| Change | Scope | Effect |
|--------|-------|--------|
| **A** | Resume-completed branch | Always fetches the reply thread and appends it to the summary preamble. Short 3-second sync timeout with clean fallback. |
| **B** | Layered preamble | `_build_completed_resume_text` accepts an optional `reply_chain_context` parameter and places it between the summary and the follow-up text. Empty/None is a no-op so prior call sites continue to emit the single-line preamble. |
| **C** | Implicit-context directive | `references_prior_context(text)` predicate + a `[CONTEXT DIRECTIVE]` block prepended when the predicate matches and the message has no `reply_to_msg_id`. |
| **D** (#1064) | Fresh-session non-Valor reply | Reply-to a non-Valor message that misses semantic routing and creates a fresh session now pre-hydrates the reply chain synchronously (same 3s timeout pattern as Change A). Stamps `extra_context["reply_chain_hydrated"]=True` on success so the worker-side idempotency guard skips the deferred fetch. Controlled by `REPLY_CHAIN_PREHYDRATION_DISABLED` kill-switch. |

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

### Fresh-Session Non-Valor Reply Arrives (Issue #1064)

When a Telegram user replies to **another user's** message and the
bridge cannot match that reply to an existing session (semantic-route
miss, no root-cache entry), a fresh session is created. Prior to
issue #1064 this path had NEITHER the `[CONTEXT DIRECTIVE]` (gated off
for reply-to messages) NOR the resume-completed pre-hydration
(scoped to `is_reply_to_valor=True`). The agent received the raw
`clean_text` with no thread context, and the deferred worker-side
enrichment had several fragile preconditions (`telegram_message_key`
indexed, `reply_to_msg_id` populated, `telegram_client` resolvable at
worker time) that could silently no-op.

```
Telegram reply (reply_to_msg_id=42, is_reply_to_valor=False,
                clean_text="engels read this and propose an issue")
        │
        ▼
bridge/telegram_bridge.py  handler
  (fresh-session branch, after semantic-route miss)
        │
        ├── is_reply_to_valor=False  (resume-completed branch skipped)
        │
        ├── semantic routing -> no_match  (fresh session created)
        │
        ├── [CONTEXT DIRECTIVE] block -> skipped (reply_to_msg_id set)
        │
        ├── os.getenv("REPLY_CHAIN_PREHYDRATION_DISABLED") ? -> skip
        │
        ├── fetch_reply_chain(client, chat_id, reply_to_msg_id, max_depth=20)
        │     with asyncio.wait_for(..., timeout=3.0)
        │     on Timeout/Exception:  logger.warning("FRESH_REPLY_CHAIN_FAIL ...")
        │                            reply_chain_context = None
        │     on success:            reply_chain_context = format_reply_chain(chain)
        │
        ├── if reply_chain_context:
        │       enqueued_message_text = f"{reply_chain_context}\n\nCURRENT MESSAGE:\n{clean_text}"
        │       extra_overrides = {"reply_chain_hydrated": True}
        │       logger.info("fresh_reply_chain_prehydrated ... chain_len=N")
        │   else:
        │       (no-op — deferred enrichment retains its chance to retry)
        │
        └── dispatch_telegram_session(
                message_text=enqueued_message_text,
                extra_context_overrides=extra_overrides,
                ...)
```

Placement is the correctness mechanism: the new block sits **after**
the resume-completed branch (which returns earlier), **after** the
semantic-routing decision (no-match), and **after** the
`[CONTEXT DIRECTIVE]` block (gated off here), so by reaching it,
control flow is guaranteed to be on the fresh-session non-Valor-reply
path. No explicit `session_id is None` check is needed.

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
| Resume-completed branch (reply-to-Valor, completed prior session) | Handler pre-hydrates synchronously (PR #953) | Handler stamps `extra_context["reply_chain_hydrated"]=True`; worker checks flag first, then falls back to `REPLY_THREAD_CONTEXT_HEADER in message_text` scan |
| Fresh-session branch (reply-to non-Valor, semantic-route miss) | Handler pre-hydrates synchronously (#1064) | Same as above — handler stamps the flag, worker's guard at `agent/session_executor.py:1045-1055` skips the deferred fetch |
| Normal new session (no reply-to, just a new thread) | Worker's deferred enrichment (`enrich_message` step 4) | Handler never pre-hydrates here, so no conflict |
| Implicit-context (no reply-to, heuristic match) | Neither adds a chain block; only the `[CONTEXT DIRECTIVE]` is prepended | N/A |

The guarantee: **the agent sees exactly one `REPLY THREAD CONTEXT` block
per prompt, regardless of which handler branch hydrated.** Regression
is prevented by
`test_no_double_hydration_when_handler_prehydrates` in
`tests/integration/test_steering.py` (parametrized across both
call sites per Implementation Note C5).

## Failure Paths

| Failure | Behavior |
|---------|----------|
| Resume-completed branch: `fetch_reply_chain` raises | `logger.warning("RESUME_REPLY_CHAIN_FAIL exception ...")` with `session_id`, `chat_id`, `reply_to_msg_id`, and `error` fields. Session still enqueues with summary-only preamble. |
| Resume-completed branch: `fetch_reply_chain` exceeds 3s | `logger.warning("RESUME_REPLY_CHAIN_FAIL timeout ...")`. Same fallback. |
| Fresh-session branch: `fetch_reply_chain` raises | `logger.warning("FRESH_REPLY_CHAIN_FAIL exception ...")` with `session_id`, `chat_id`, `reply_to_msg_id`, and `error` fields. Session enqueues with raw `clean_text`; `reply_chain_hydrated` flag NOT stamped, so worker-side deferred enrichment remains free to retry. |
| Fresh-session branch: `fetch_reply_chain` exceeds 3s | `logger.warning("FRESH_REPLY_CHAIN_FAIL timeout ...")`. Same fallback semantics as exception branch. |
| `format_reply_chain([])` | Returns `""`. Handler does NOT stamp `reply_chain_hydrated` and does NOT modify `message_text` — worker's deferred enrichment will confirm the empty chain on its own retry, not a silent dead zone (Impl Note C2). |
| `references_prior_context(None)` / `""` / `"   "` / non-string | Returns `False` — no directive injection. |
| `REPLY_CONTEXT_DIRECTIVE_DISABLED=1` | Directive injection is skipped regardless of heuristic match (no code deploy required). |
| `REPLY_CHAIN_PREHYDRATION_DISABLED=1` | Fresh-session pre-hydration is skipped; fallback is worker-side deferred enrichment (no code deploy required). |

## Key Files

- `bridge/context.py` — `REPLY_THREAD_CONTEXT_HEADER` constant,
  `STATUS_QUESTION_PATTERNS`, `DEICTIC_CONTEXT_PATTERNS`,
  `references_prior_context`, `matched_context_patterns`,
  `fetch_reply_chain`, `format_reply_chain`.
- `bridge/telegram_bridge.py` — `_build_completed_resume_text`
  (accepts `reply_chain_context`), resume-completed handler branch,
  implicit-context directive injection, fresh-session non-Valor
  pre-hydration block (issue #1064).
- `agent/session_executor.py` — Deferred-enrichment idempotency guard
  (skips reply-chain fetch when either `extra_context["reply_chain_hydrated"]`
  is truthy or the canonical header is present in `message_text`).
  Moved here from `agent/agent_session_queue.py` in the PR #1023 split.

## Rollback

All four changes are additive. Rollback path:

1. Set `REPLY_CONTEXT_DIRECTIVE_DISABLED=1` to turn off Change C (implicit-context directive) without a deploy.
2. Set `REPLY_CHAIN_PREHYDRATION_DISABLED=1` to turn off the fresh-session pre-hydration (issue #1064) without a deploy.
3. Revert `_build_completed_resume_text` and its caller in the handler
   to restore summary-only hydration (Changes A + B).
4. The constant, `references_prior_context`, and the idempotency guard
   can stay — they are inert without the handler call sites.

No schema changes, no migrations. A full revert restores pre-PR behavior
bit-for-bit.

## Tests

- `tests/unit/test_context_helpers.py` — 45 tests covering the
  `references_prior_context` contract, deictic/status pattern matches,
  negative guards (None/empty/whitespace/non-string), and
  `_build_completed_resume_text` layering.
- `tests/integration/test_steering.py::TestResolveRootSessionId` —
  covers both the PR #953 resume-completed branch and the issue #1064
  fresh-session branch:
    - `test_reply_to_completed_session_fallback_without_summary`
    - `test_resume_completed_carries_reply_chain`
    - `test_no_double_hydration_when_handler_prehydrates`
      (parametrized: `resume_completed`, `fresh_session_non_valor`)
    - `test_reply_chain_fetch_failure_falls_back`
      (parametrized: `resume_completed` / `RESUME_REPLY_CHAIN_FAIL`,
      `fresh_session_non_valor` / `FRESH_REPLY_CHAIN_FAIL`)
    - `test_implicit_context_directive_injected`
    - `test_fresh_session_non_valor_reply_prehydrates_chain` (#1064)
    - `test_fresh_session_non_valor_reply_timeout_falls_back` (#1064)
    - `test_fresh_session_reply_to_valor_skips_new_block` (#1064)
    - `test_fresh_session_prehydration_kill_switch` (#1064)

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
