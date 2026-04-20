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

When a Telegram-triggered session tries to stop:

1. **First stop** — the hook reads the agent's raw output from the transcript, runs it through the message drafter to produce a draft, then blocks the stop with a review prompt showing the draft and delivery choices
2. **Agent decides** — the agent sees its draft and picks one of:
   - `SEND` — deliver the draft as-is
   - `EDIT: <text>` — replace the draft with revised text
   - `REACT: <emoji>` — respond with only an emoji reaction
   - `SILENT` — send nothing at all
   - `CONTINUE` — resume working (false stop detected)
3. **Second stop** — the hook parses the choice, writes delivery instructions to the AgentSession, and allows completion
4. **Bridge executes** — `send_response_with_files()` reads the delivery instruction and acts accordingly

### Activation Rules

The review gate only fires when:
- Session was triggered by a Telegram message (`TELEGRAM_CHAT_ID` + `TELEGRAM_REPLY_TO` env vars set)
- Agent hasn't already self-messaged via PM tools (`has_pm_messages()` is false)
- Session has non-empty output

Skipped for: subagent sessions, programmatic sessions, local Claude Code sessions, PM self-messaging sessions.

### False Stop Detection

Simple heuristic: if the agent's output is short (<500 chars) and contains promise-like patterns ("I started...", "Let me check...", "I'm going to..."), the review prompt suggests CONTINUE. This is a suggestion, not forced — the agent decides.

## Delivery Execution (tool-call path)

Post-#1072, the stop hook no longer writes delivery fields to the AgentSession. Instead, the agent acts on its delivery choice by calling a tool directly during the second stop:

| Agent choice | Action |
|--------------|--------|
| `SEND` | Call `tools/send_message.py` with the drafted text — payload flows through `TelegramRelayOutputHandler.send`, which always routes through `bridge.message_drafter.draft_message` before the outbox write |
| `EDIT: <text>` | Same as `SEND` but with the agent's revised text |
| `REACT: <emoji>` | Call `tools/react_with_emoji.py` with the chosen emoji — no text is sent |
| `SILENT` | Do nothing — no tool calls, session completes without output |
| `CONTINUE` | Resume working; the hook does not stop the agent |

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
- `tests/unit/test_delivery_execution.py` — send/react/silent/fallthrough paths
- `tests/unit/test_qa_handler.py` — Teammate prompt humility markers, review gate awareness
- `tests/e2e/test_message_pipeline.py` — Bool classifier assertions

## Related

- [Teammate Conversational Humility](qa-conversational-humility.md) — Teammate prompt design
- [Config-Driven Chat Mode](config-driven-chat-mode.md) — Persona routing
- [Chat-Dev Session Architecture](pm-dev-session-architecture.md) — Session types
- [Agent-Controlled Delivery Protocol](agent-controlled-delivery.md) — Defense-in-depth filtering preventing delivery-choice text leaking to users
- Issue [#589](https://github.com/tomcounsell/ai/issues/589) — Tracking issue
