# Agent-Controlled Message Delivery

## Overview

Gives the agent final say over its own output before it reaches the user. Instead of raw text flowing blindly through the message drafter to Telegram, the agent reviews a draft of its response and chooses how to deliver it.

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

When a user-triggered session tries to stop:

1. **First stop** — the hook reads the agent's raw output from the transcript, runs it through the message drafter to produce a draft, then blocks the stop with a review prompt showing the draft and a prepopulated tool-call presentation (see [Delivery Execution](#delivery-execution-tool-call-path) below for the exact contract).
2. **Agent acts** — the agent invokes a delivery tool (`tools/send_message.py`, `tools/react_with_emoji.py`), stops silently, or continues working. There is no string-menu protocol — the agent's choice is the tool call itself.
3. **Second stop** — the hook inspects the transcript tail for `tool_use` blocks via `classify_delivery_outcome()`, classifies the outcome (send / react / silent / continue), and either allows completion or re-blocks with a "resume work" prompt for `continue`.

The hook does **not** write a `delivery_action` or `delivery_text` field to the `AgentSession` — delivery is driven entirely by the tool call the agent makes during the second stop. Tool-call payloads route through `TelegramRelayOutputHandler.send` (or `EmailOutputHandler.send`) which always runs the drafter before the outbox write.

### Activation Rules

The review gate only fires when:
- Session has a user-visible transport configured — `_is_user_triggered()` checks for any of `TELEGRAM_CHAT_ID`, `EMAIL_REPLY_TO`, or `VALOR_TRANSPORT` (`agent/hooks/stop.py:63-74`)
- Session is not a child session (`parent_agent_session_id` unset; children deliver via the parent)
- Session has non-empty transcript output

Skipped for: subagent sessions, programmatic sessions, and any session without one of the transport env vars above.

### False Stop Detection

Simple heuristic: if the agent's output is short (<500 chars) and contains promise-like patterns ("I started...", "Let me check...", "I'm going to..."), the review prompt suggests CONTINUE. This is a suggestion, not forced — the agent decides.

## Delivery Execution (tool-call path)

Post-#1072 the stop hook does not write delivery fields to the `AgentSession`. The agent's delivery choice is the tool call it makes (or doesn't) during the second stop. `classify_delivery_outcome()` (`agent/hooks/stop.py:217-245`) inspects the transcript tail and maps the observed `tool_use` blocks to one of four outcomes:

| Classified outcome | Agent action that produces it | Effect |
|--------------------|-------------------------------|--------|
| `send` | Invoked `python tools/send_message.py "<text>"` (the draft as-is, or a revised text — both classify the same) — also matches the legacy `tools/send_telegram.py` for the PM self-messaging path | Payload flows through `TelegramRelayOutputHandler.send` (or `EmailOutputHandler.send`), which always routes through `bridge.message_drafter.draft_message` before the outbox write |
| `react` | Invoked `python tools/react_with_emoji.py "<feeling>"` | Telegram reaction is set on the original message; no text sent |
| `silent` | Stopped without any tool invocation | Session completes with no output |
| `continue` | Other `tool_use` activity present (still working) | Hook re-blocks with a "resume work" prompt; review state is reset so the next stop re-enters the gate |

The canonical drafter entry point is `TelegramRelayOutputHandler.send` (for Telegram) and `EmailOutputHandler.send` (for email). Both run the drafter unconditionally, with a `try/except` fallback to raw text on drafter failure. See [message-drafter.md](message-drafter.md) for drafter details.

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
- `tests/unit/test_tool_call_delivery.py` — `classify_delivery_outcome`'s send/react/continue/silent outcomes (plus the legacy `send_telegram` alias) and the second-stop tool-call review-gate flow
- `tests/unit/test_duplicate_delivery.py` — Duplicate-delivery prevention: catchup Redis dedup checks and auto-continue skips for completed sessions
- `tests/unit/test_qa_handler.py` — Teammate prompt humility markers, review gate awareness
- `tests/e2e/test_message_pipeline.py` — Bool classifier assertions

## Related

- [Teammate Conversational Humility](qa-conversational-humility.md) — Teammate prompt design
- [Config-Driven Chat Mode](config-driven-chat-mode.md) — Persona routing
- [Chat-Dev Session Architecture](pm-dev-session-architecture.md) — Session types
- [PM Final Delivery](pm-final-delivery.md) — SDLC terminal-turn delivery protocol (bypasses the review gate)
- Issue [#589](https://github.com/tomcounsell/ai/issues/589) — Tracking issue
