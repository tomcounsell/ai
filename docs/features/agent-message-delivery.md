# Agent-Controlled Message Delivery

## Overview

Gives the agent final say over its own output before it reaches the user. Instead of raw text flowing blindly through the message drafter to Telegram, the agent reviews a draft of its response and chooses how to deliver it.

**Correction (2026-07-09, issues #1955 / #1370):** the `agent/hooks/stop.py` "Stop Hook Review Gate" described below is **dead code for every session executed through `agent/session_runner/`** — both `session_type="eng"` (PM/Dev roles) and `session_type="teammate"` sessions. `agent/session_runner/hook_edge.py::generate_hook_settings` wires the Stop hook only to `hook_forwarder.py`, never to `agent/hooks/stop.py`; confirmed independently twice (once while building the local-file-path validator, [issue #1955](https://github.com/tomcounsell/ai/issues/1955), once during the freshness re-check of `docs/plans/consolidate_delivery_paths.md`). The section below is retained as historical/architectural documentation of the gate's design (it may still run for non-session_runner code paths), but **it is not the live violation-surfacing mechanism for production eng/teammate traffic today.** The live mechanism is the **self-draft steering path** (`_inject_self_draft_steering` in `agent/output_handler.py`, `:429-441`) described under [Filters layered on every send](#filters-layered-on-every-send) — every `Violation` the drafter computes (wire-format or, since #1955, local-file-path references) reaches the agent through that path, regardless of whether the stop-hook gate ever fires.

## How It Works

### PM Final Delivery (SDLC terminal turn)

Teammate and ad-hoc agent sessions use the review-gate path below. **PM
sessions running SDLC work follow a different final-delivery protocol:**
when the pipeline reaches a terminal state (per
`agent.pipeline_complete.is_pipeline_complete`), the worker invokes a
dedicated "compose final summary" harness turn via
`agent.session_completion._deliver_pipeline_completion`. The runner owns
the final delivery end-to-end, bypassing the nudge loop and the review
gate. See `docs/features/pm-final-delivery.md` for the full protocol
(issue #1058 replaces the earlier `[PIPELINE_COMPLETE]` marker).

### Stop Hook Review Gate (`agent/hooks/stop.py`)

> **Dead for session_runner sessions.** See the correction note in [Overview](#overview). `agent/session_runner/hook_edge.py::generate_hook_settings` never wires the Stop hook to this file for `eng`/`teammate` sessions — the steps below do not execute for that traffic. Read this section as the gate's design, not its current reachability.

When a user-triggered session tries to stop:

1. **First stop** — the hook reads the agent's raw output from the transcript, passes it through the message drafter (which validates and composes the agent's verbatim text — no server-side LLM rewrite), then blocks the stop with a review prompt showing the draft and a prepopulated tool-call presentation (see [Delivery Execution](#delivery-execution-tool-call-path) below for the exact contract).
2. **Agent acts** — the agent invokes a delivery tool (`tools/send_message.py`, `tools/react_with_emoji.py`), stops silently, or continues working. There is no string-menu protocol — the agent's choice is the tool call itself.
3. **Second stop** — the hook inspects the transcript tail for `tool_use` blocks via `classify_delivery_outcome()`, classifies the outcome (send / react / silent / continue), and either allows completion or re-blocks with a "resume work" prompt for `continue`.

The hook does **not** write a `delivery_action` or `delivery_text` field to the `AgentSession` — delivery is driven entirely by the tool call the agent makes during the second stop. Tool-call payloads route through `TelegramRelayOutputHandler.send` — the single canonical queue-side handler for both telegram and email transports. The handler runs the drafter once before the transport branch, so both outbox writes inherit the same drafter / redundancy filter / read-the-room / narration-fallback pipeline. See [Filters layered on every send](#filters-layered-on-every-send).

### Activation Rules

The review gate only fires when:
- Session has a user-visible transport configured — `_is_user_triggered()` checks for any of `TELEGRAM_CHAT_ID`, `EMAIL_REPLY_TO`, or `VALOR_TRANSPORT` (`agent/hooks/stop.py:63-74`)
- Session is not a child session (`parent_agent_session_id` unset; children deliver via the parent)
- Session has non-empty transcript output

Skipped for: subagent sessions, programmatic sessions, and any session without one of the transport env vars above.

**`VALOR_TRANSPORT` accepted values:** `telegram` or `email` (case-insensitive). When set, it overrides the inferred transport (which otherwise picks `email` if `EMAIL_REPLY_TO` is set, else `telegram` when `TELEGRAM_CHAT_ID` is set). Any other value is rejected by the tool with a non-zero exit.

### False Stop Detection

Simple heuristic: if the agent's output is short (<500 chars) and contains promise-like patterns ("I started...", "Let me check...", "I'm going to..."), the review prompt suggests CONTINUE. This is a suggestion, not forced — the agent decides.

## Delivery Execution (tool-call path)

Post-#1072 the stop hook does not write delivery fields to the `AgentSession`. The agent's delivery choice is the tool call it makes (or doesn't) during the second stop. `classify_delivery_outcome()` (`agent/hooks/stop.py:217-245`) inspects the transcript tail and maps the observed `tool_use` blocks to one of four outcomes:

| Classified outcome | Agent action that produces it | Effect |
|--------------------|-------------------------------|--------|
| `send` | Invoked `python tools/send_message.py "<text>"` (the draft as-is, or a revised text — both classify the same) | Payload flows through `TelegramRelayOutputHandler.send` (single canonical handler for both telegram and email), which passes the text through `bridge.message_drafter.draft_message` exactly once (verbatim pass-through with validation, no LLM rewrite) before the outbox write |
| `react` | Invoked `python tools/react_with_emoji.py "<feeling>"` | Telegram reaction is set on the original message; no text sent |
| `silent` | Stopped without any tool invocation | Session completes with no output |
| `continue` | Other `tool_use` activity present (still working) | Hook re-blocks with a "resume work" prompt; review state is reset so the next stop re-enters the gate |

The canonical drafter entry point is `TelegramRelayOutputHandler.send`. Despite the type name, it is the single canonical queue-side entrypoint for both telegram and email transports — the handler hoists the drafter to a single call site above the transport branch, so the email outbox write inherits identically-validated text without a second drafter call. The drafter passes through the agent's verbatim text (after narration stripping and composition); it does not rewrite via Haiku or any other LLM. Drafter failures fall through to raw text via a `try/except`; the relay length guard catches oversize payloads as a last line of defense. See [message-drafter.md](message-drafter.md) for drafter details.

The synchronous SMTP path in `bridge/email_bridge.py::EmailOutputHandler.send` continues to exist for the silent worker registration on email-routing projects (`worker/__main__.py`). The CLI tool (`tools/send_message.py`) never imports `EmailOutputHandler`; the SMTP layer is the wrong abstraction for a queue-only writer.

### Filters layered on every send

Both the silent worker path and the CLI tool-call path (`tools/send_message.py`) reach `TelegramRelayOutputHandler.send`, which runs these filters in order on every invocation:

1. **Drafter** (`bridge.message_drafter.draft_message`) — pass-through with validation. The agent's own text is used verbatim after narration stripping and structural composition. No server-side LLM rewrite. Runs once, before the transport branch, with `medium="telegram"` or `medium="email"` based on `extra_context.transport`.
2. **Self-draft steering** (PRIMARY flag-handling path, and — since #1955 — the sole live violation-surfacing mechanism for session_runner sessions; see the Overview correction) — when the drafter sets `needs_self_draft=True` (any non-empty `violations` list — wire-format violation, local file-path reference — or empty promise detected), `_inject_self_draft_steering(session, draft)` pushes a nudge back to the authoring agent asking it to rewrite. When `draft.violations` includes a `local_file_path_reference` entry, the pushed instruction gets a targeted addendum directing the agent to attach the file via `tools/send_message.py "<caption>" --file <path>` instead of re-pasting the dead local path (see [Message Drafter §Steering-first flag handling](message-drafter.md#steering-first-flag-handling)). The attempt count is tracked at `steering:attempts:{session_id}` in Redis (cap: `SELF_DRAFT_MAX_ATTEMPTS = 2`). On cap hit, falls through to the narration fallback. **Persist-at-defer-time (issue #1730):** before skipping the outbox write, the handler persists `deferred_self_draft_pending=True` and `deferred_self_draft_text=<original text>` into `AgentSession.extra_context` via a safe read-modify-write. The held text is recovered on **all** terminal paths including a clean `completed`: the synchronous helper `flush_deferred_self_draft_sync` in `agent/session_health.py` fires at the `finalize_session` chokepoint for **telegram** sessions on all terminal statuses and for **email** sessions on the `completed` path (issues #1794, #1797); the async helper `_deliver_deferred_self_draft_fallback` covers **email** sessions on `failed`/`abandoned`. See [Session Lifecycle §Deferred Self-Draft Fallback Delivery](session-lifecycle.md#deferred-self-draft-fallback-delivery-issues-1730-1794-1797).
3. **Redundancy filter** ([`bridge/redundancy_filter.py`](../../bridge/redundancy_filter.py)) — deterministic bigram-Jaccard guard for SDLC sessions, compares the drafted text against `session.recent_sent_drafts`. On `suppress` the payload is dropped; for telegram the handler queues a 👀 reaction on the anchor message.
4. **Read-the-Room** ([`bridge/read_the_room.py`](../../bridge/read_the_room.py)) — Haiku-judged appropriateness gate (`READ_THE_ROOM_ENABLED`). Returns `send` | `trim` | `suppress`; on `suppress` for telegram the handler queues a 👀 reaction and skips the outbox.
5. **Narration fallback** — when steering is exhausted (cap hit) or unavailable, the handler substitutes a fixed message rather than emitting pure process narration.
6. **Outbox rpush** — `telegram:outbox:{session_id}` or `email:outbox:{session_id}` (the latter carries a reply-all `to` list).

For email sessions, suppression (RTR or redundancy) drops the payload entirely with no reaction — email has no equivalent reaction mechanism. The CLI tool's promise gate runs **before** the handler call (gate → linkify → handler-drafter → handler-filters → outbox) so a session with an outstanding promise short-circuits without paying the Haiku / Popoto / Redis cost.

### Diagnostic fallback: `ALLOW_LEGACY_RPUSH_FALLBACK`

When the CLI tool cannot reconstitute its `AgentSession` from Popoto (race, dev environment, or misconfiguration), the **default behavior is fail-closed** — the tool exits non-zero so the harness sees the failure. Setting `ALLOW_LEGACY_RPUSH_FALLBACK=1` opts into a raw-rpush fallback path that bypasses the canonical handler (no drafter, no RTR, no redundancy filter) and logs a warning. This is intended for short-lived diagnostic use only; never set in production worker env.

## Classification Context (`agent/sdk_client.py`)

The routing classification result is passed as advisory text in the agent's enriched message:

```
[Routing context: classified as teammate (classifier confidence=92%). This is an initial guess — use your judgment.]
```

Both Teammate and PM sessions receive this context. The agent can disagree with the classification and act accordingly.

## Teammate Prompt (`agent/teammate_handler.py`)

The Teammate persona prompt includes a DELIVERY REVIEW section explaining the choices. Combined with conversational humility rules (hedged language, clarification-first, multi-perspective brevity), this gives the Teammate agent full control over tone and delivery.

## Test Coverage

- `tests/unit/test_stop_hook_review.py` — Review gate activation, transcript reading, false stop detection, choice parsing, state management, integration tests
- `tests/unit/test_tool_call_delivery.py` — `classify_delivery_outcome`'s send/react/continue/silent outcomes, the second-stop tool-call review-gate flow, and the tool → canonical-handler routing assertions for both transports
- `tests/unit/test_duplicate_delivery.py` — Duplicate-delivery prevention: catchup Redis dedup checks and auto-continue skips for completed sessions
- `tests/unit/test_qa_handler.py` — Teammate prompt humility markers, review gate awareness
- `tests/unit/test_output_handler.py` — `TestDrafterHoistedAboveTransport`: the drafter is invoked exactly once for both telegram and email sessions; email payload carries the reply-all `to` list; CLI-supplied file paths propagate to both outboxes. `TestDeferredSelfDraftPersistence`: `deferred_self_draft_pending` and `deferred_self_draft_text` are persisted to `AgentSession.extra_context` on self-draft defer.

## Related

- [Teammate Conversational Humility](qa-conversational-humility.md) — Teammate prompt design
- [Config-Driven Chat Mode](config-driven-chat-mode.md) — Persona routing
- [Eng Session Architecture](eng-session-architecture.md) — Session types
- [PM Final Delivery](pm-final-delivery.md) — SDLC terminal-turn delivery protocol (bypasses the review gate)
- [Message Drafter](message-drafter.md) — `detect_local_file_reference` validator, violation promotion, and the violation-aware self-draft steering addendum (issue #1955)
- `docs/plans/consolidate_delivery_paths.md` — active plan (#1370) proposing a declared canonical delivery-path registry; its Freshness Check independently confirmed the stop-hook gate is dead for session_runner sessions
- Issue [#1955](https://github.com/tomcounsell/ai/issues/1955) — local file-path flagging fix; source of the stop-hook-gate-is-dead correction above
- Issue [#589](https://github.com/tomcounsell/ai/issues/589) — Tracking issue
