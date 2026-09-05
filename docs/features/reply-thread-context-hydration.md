# Reply-Thread Context Hydration

**Status:** Shipped
**Tracking:** [#949](https://github.com/tomcounsell/ai/issues/949), [#1064](https://github.com/tomcounsell/ai/issues/1064)
**Plans:** [`docs/plans/reply_thread_context_hydration.md`](../plans/reply_thread_context_hydration.md), [`docs/plans/reply_chain_fresh_session.md`](../plans/reply_chain_fresh_session.md)

## Problem

Telegram replies — either via the native Reply feature or by referencing prior state
("did we get that fixed?", "the bug is still broken") — must reach the agent with their
thread context intact, or the agent re-asks for information already visible earlier in
the chat. The system carries this context through four paths: the resume-completed
branch, deferred message enrichment, implicit context references, and fresh sessions
created from a reply to a non-Valor message.

## Solution Overview

Four coordinated changes across `bridge/context.py`,
`bridge/telegram_bridge.py`, and `agent/session_executor.py`:

| Change | Scope | Effect |
|--------|-------|--------|
| **A** | Resume-completed branch | Always fetches the reply thread and appends it to the summary preamble. Short 3-second sync timeout with clean fallback. |
| **B** | Layered preamble | `_build_completed_resume_text` accepts an optional `reply_chain_context` parameter and places it between the summary and the follow-up text. Empty/None is a no-op, so call sites that omit the parameter emit the single-line preamble. |
| **C** | Implicit-context directive | `references_prior_context(text)` predicate + a `[CONTEXT DIRECTIVE]` block prepended when the predicate matches and the message has no `reply_to_msg_id`. |
| **D** | Fresh-session non-Valor reply | Reply-to a non-Valor message that misses semantic routing and creates a fresh session pre-hydrates the reply chain synchronously (same 3s timeout pattern as Change A). Stamps `extra_context["reply_chain_hydrated"]=True` on success so the worker-side idempotency guard skips the deferred fetch. Controlled by `REPLY_CHAIN_PREHYDRATION_DISABLED` kill-switch. |

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

## Chain-Ancestor Media Rendering

Chain-ancestor file media — a photo, voice note, or document attached to one
of the messages `fetch_reply_chain` walks — is rendered, never dropped.
`_resolve_media_descriptor` (`bridge/context.py`) looks up the hop's
`TelegramMessage` record and hands its path and download error to
`telegram_media_descriptor`, the same producer the live Telegram intake uses
for the trigger message; `format_reply_chain` composes the result into the
rendered line through `format_media_descriptor`. The descriptor shape
(`media_descriptor`), the path classifier (`describe_local_media`), and the
renderer are shared with the email intake as well, so a file reads the same
to the agent whether it sits on a chain ancestor, on the current Telegram
message, or on an email (see [Bridge/Worker
Architecture](bridge-worker-architecture.md#medium-parity-for-inbound-attachments)).
Descriptor eligibility is decided by `get_media_type` (`bridge/media.py`),
the same classifier the download path uses: only photo and document kinds
ever resolve, so the renderer can never claim a file exists for media the
bridge could never have downloaded. Four outcomes, each textually
distinguishable:

1. **Resolved.** The ancestor's `TelegramMessage` record carries a
   `media_local_path` that stats readable. The line carries the caption (if
   any) plus the descriptor — filename, media type, and the absolute path as
   a machine-facing affordance, e.g. `[attachment: report.pdf (document) at
   machine path /Users/.../data/media/document_..._123.pdf]`. A caption
   composes with the descriptor rather than replacing it, so the agent sees
   both the human's note and the file.
2. **Referenced but unreadable.** Media exists per Telethon but the on-disk
   path cannot be trusted: no `TelegramMessage` record (`no_record`), no
   path recorded (`no_path_recorded`), a persisted download error
   (`download_error: ...`), the file missing from disk (`file_missing`), a
   path outside `MEDIA_DIR` (`invalid_path`), or an unexpected resolution
   failure (`resolution_error`). The line names the file and the reason,
   e.g. `[unreadable attachment: voice-456 (voice) reason:
   no_path_recorded]`, so the agent can report the gap precisely instead of
   guessing. Valor's own outbound media hops always render this way:
   outbound stores never set `media_local_path`, so a file Valor sent is
   honestly unreadable rather than silently dropped.
3. **Text only.** No media on the hop. Renders exactly as it always has.
4. **Non-file media.** Link previews, polls, geo and live locations, venues,
   contacts, dice, games, invoices, and every other kind `get_media_type`
   declines yield no descriptor: the hop renders its text alone, and a
   caption-less such hop renders nothing at all (the same skip applied to
   empty hops generally). This is what keeps a shared live location from
   producing a false `[unreadable attachment]` claim.

The session-routing walk (`resolve_root_session_id`) consumes only sender
and message id, so it calls `fetch_reply_chain(..., resolve_media=False)`
and skips descriptor resolution — and its Redis lookups — entirely.

**Chat scoping.** The path lookup filters
`TelegramMessage.query.filter(chat_id=str(chat_id), message_id=msg.id)` —
the same shape used elsewhere in this module. `data/media/` is one flat
directory shared by every chat and project on the machine, and Telegram
message ids are per-chat sequences, so a record with the same `message_id`
in a different chat can genuinely exist; the `chat_id` filter is what keeps
that record from ever answering a lookup for the wrong chat. The resolver
never globs `MEDIA_DIR` for a match — the scoped `TelegramMessage` record is
the only path into a descriptor.

**Path disclosure.** A resolved descriptor carries the file's absolute path
so the agent can read it in one call. That path is a machine-facing
affordance, not the label meant for a human eye: the descriptor's
human-readable name is the file's basename, so quoting the attachment back
into a chat naturally reproduces the filename rather than the surrounding
directory structure. This is a disclosure-of-shape risk, not a cross-tenant
one — chat scoping above is what keeps a file from surfacing in the wrong
chat at all.

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
        │       telegram_message_key=stored_msg_id,
        │       ...)
        │
        └── record_message_processed(chat_id, msg_id)
```

The worker later runs deferred enrichment. If the canonical header is
already present in `session.message_text` the reply-chain fetch is
skipped — media, YouTube, and link summaries still run normally. This is
the idempotency guard for Race 1.

### Fresh-Session Non-Valor Reply Arrives

When a Telegram user replies to **another user's** message and the
bridge cannot match that reply to an existing session (semantic-route
miss, no root-cache entry), a fresh session is created. The handler
pre-hydrates the reply chain synchronously, rather than relying on the
`[CONTEXT DIRECTIVE]` (gated off for reply-to messages) or the
resume-completed pre-hydration (scoped to `is_reply_to_valor=True`).
The deferred worker-side enrichment remains the fallback when the
handler-side fetch cannot run.

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

Placement is the correctness mechanism: the block sits **after**
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
| Resume-completed branch (reply-to-Valor, completed prior session) | Handler pre-hydrates synchronously | Handler stamps `extra_context["reply_chain_hydrated"]=True`; worker checks flag first, then falls back to `REPLY_THREAD_CONTEXT_HEADER in message_text` scan |
| Fresh-session branch (reply-to non-Valor, semantic-route miss) | Handler pre-hydrates synchronously | Same as above — handler stamps the flag, worker's guard at `agent/session_executor.py:1045-1055` skips the deferred fetch |
| Normal new session (no reply-to, just a new thread) | Worker's deferred enrichment (`enrich_message` step 4) | Handler never pre-hydrates here, so no conflict |
| Implicit-context (no reply-to, heuristic match) | Neither adds a chain block; only the `[CONTEXT DIRECTIVE]` is prepended | N/A |

The guarantee: **the agent sees exactly one `REPLY THREAD CONTEXT` block
per prompt, regardless of which handler branch hydrated.** Regression
is prevented by
`test_no_double_hydration_when_handler_prehydrates` in
`tests/integration/test_steering.py` (parametrized across both
call sites).

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
  pre-hydration block.
- `agent/session_executor.py` — Deferred-enrichment idempotency guard
  (skips reply-chain fetch when either `extra_context["reply_chain_hydrated"]`
  is truthy or the canonical header is present in `message_text`).

## Kill Switches

The feature is fully kill-switchable without a deploy:

1. Set `REPLY_CONTEXT_DIRECTIVE_DISABLED=1` to turn off the implicit-context directive.
2. Set `REPLY_CHAIN_PREHYDRATION_DISABLED=1` to turn off the fresh-session pre-hydration; the worker-side deferred enrichment remains as the fallback.

## Tests

- `tests/unit/test_context_helpers.py` — 45 tests covering the
  `references_prior_context` contract, deictic/status pattern matches,
  negative guards (None/empty/whitespace/non-string), and
  `_build_completed_resume_text` layering.
- `tests/integration/test_steering.py::TestResolveRootSessionId` —
  covers both the resume-completed branch and the fresh-session branch:
    - `test_reply_to_completed_session_fallback_without_summary`
    - `test_resume_completed_carries_reply_chain`
    - `test_no_double_hydration_when_handler_prehydrates`
      (parametrized: `resume_completed`, `fresh_session_non_valor`)
    - `test_reply_chain_fetch_failure_falls_back`
      (parametrized: `resume_completed` / `RESUME_REPLY_CHAIN_FAIL`,
      `fresh_session_non_valor` / `FRESH_REPLY_CHAIN_FAIL`)
    - `test_implicit_context_directive_injected`
    - `test_fresh_session_non_valor_reply_prehydrates_chain`
    - `test_fresh_session_non_valor_reply_timeout_falls_back`
    - `test_fresh_session_reply_to_valor_skips_new_block`
    - `test_fresh_session_prehydration_kill_switch`

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
